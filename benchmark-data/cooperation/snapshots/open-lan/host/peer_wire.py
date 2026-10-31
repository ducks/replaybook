"""A bounded two-port QEMU Ethernet wire with controller-owned packet capture."""
from __future__ import annotations

import json
from pathlib import Path
import selectors
import socket
import struct
import threading
import time


class PeerWire:
    def __init__(self, directory: Path, max_bytes=64*1024*1024, max_frames=1000000):
        self.directory=directory
        self.directory.mkdir(parents=True,exist_ok=True)
        self.max_bytes=max_bytes
        self.max_frames=max_frames
        self.selector=selectors.DefaultSelector()
        self.listeners={}
        self.clients={}
        self.buffers={'alpha':bytearray(),'beta':bytearray()}
        self.pending={'alpha':bytearray(),'beta':bytearray()}
        self.frames=0
        self.bytes=0
        self.failure=None
        self.closed=False
        self.stop_event=threading.Event()
        for node in ('alpha','beta'):
            listener=socket.socket()
            listener.bind(('127.0.0.1',0))
            listener.listen(1)
            listener.setblocking(False)
            self.listeners[node]=listener
            self.selector.register(listener,selectors.EVENT_READ,('listen',node))
        self.capture=(directory/'peer.pcap').open('xb')
        self.capture.write(struct.pack('<IHHIIII',0xa1b2c3d4,2,4,0,0,65535,1))
        self.thread=threading.Thread(target=self.run,daemon=True)

    @property
    def ports(self):
        return {node:sock.getsockname()[1] for node,sock in self.listeners.items()}

    def start(self):
        self.thread.start()
        return self

    def run(self):
        try:
            while not self.stop_event.is_set():
                for key,mask in self.selector.select(.1):
                    kind,node=key.data
                    if kind=='listen':
                        connection,_=key.fileobj.accept()
                        connection.setblocking(False)
                        if node in self.clients:
                            connection.close()
                            raise ValueError('duplicate peer port connection')
                        self.clients[node]=connection
                        self.selector.register(connection,selectors.EVENT_READ,('client',node))
                        # Stop accepting extra connections for this VM port.
                        self.selector.unregister(key.fileobj)
                        if self.pending[node]:
                            self.selector.modify(connection,selectors.EVENT_READ|selectors.EVENT_WRITE,('client',node))
                        continue
                    connection=key.fileobj
                    if mask&selectors.EVENT_READ:
                        chunk=connection.recv(65536)
                        if not chunk:
                            self.selector.unregister(connection)
                            connection.close()
                            del self.clients[node]
                            self.stop_event.set()
                            break
                        self.buffers[node].extend(chunk)
                        while len(self.buffers[node])>=4:
                            size=struct.unpack('!I',self.buffers[node][:4])[0]
                            if not 14<=size<=65535:
                                raise ValueError('invalid Ethernet frame length')
                            if len(self.buffers[node])<size+4:
                                break
                            frame=bytes(self.buffers[node][4:4+size])
                            del self.buffers[node][:4+size]
                            self.frames+=1
                            self.bytes+=size
                            if self.frames>self.max_frames or self.bytes>self.max_bytes:
                                raise ValueError('peer traffic exceeded capture budget')
                            now=time.time();seconds=int(now)
                            self.capture.write(struct.pack('<IIII',seconds,int((now-seconds)*1e6),size,size)+frame)
                            self.capture.flush()
                            other='beta' if node=='alpha' else 'alpha'
                            self.pending[other].extend(struct.pack('!I',size)+frame)
                            if len(self.pending[other])>1024*1024:
                                raise ValueError('peer is not draining the wire')
                            if other in self.clients:
                                self.selector.modify(self.clients[other],selectors.EVENT_READ|selectors.EVENT_WRITE,('client',other))
                    if mask&selectors.EVENT_WRITE and self.pending[node]:
                        sent=connection.send(self.pending[node])
                        del self.pending[node][:sent]
                        if not self.pending[node]:
                            self.selector.modify(connection,selectors.EVENT_READ,('client',node))
        except Exception as error:
            self.failure=f'{type(error).__name__}: {error}'
            self.stop_event.set()
        finally:
            self.capture.flush()

    def close(self):
        if self.closed:
            return
        self.closed=True
        self.stop_event.set()
        self.thread.join(3)
        for connection in list(self.clients.values())+list(self.listeners.values()):
            connection.close()
        self.selector.close()
        self.capture.close()
        (self.directory/'wire.json').write_text(json.dumps({'frames':self.frames,'bytes':self.bytes,'failure':self.failure},indent=2)+'\n')


def tcp_streams(path: Path):
    """Read this wire's classic little-endian Ethernet PCAP; reassemble TCP payloads."""
    streams={}
    with path.open('rb') as source:
        header=source.read(24)
        if len(header)!=24 or struct.unpack('<I',header[:4])[0]!=0xa1b2c3d4:
            raise ValueError('unsupported packet capture')
        while record:=source.read(16):
            if len(record)!=16:
                raise ValueError('truncated capture record')
            seconds,micros,size,original=struct.unpack('<IIII',record)
            if size>65535 or original!=size:
                raise ValueError('invalid capture record')
            frame=source.read(size)
            if len(frame)!=size:
                raise ValueError('truncated Ethernet frame')
            if len(frame)<54 or frame[12:14]!=b'\x08\x00' or frame[23]!=6:
                continue
            ip=14;ihl=(frame[ip]&15)*4
            if ihl<20 or len(frame)<ip+ihl+20:
                continue
            total=struct.unpack('!H',frame[ip+2:ip+4])[0]
            tcp=ip+ihl;offset=(frame[tcp+12]>>4)*4
            if offset<20 or tcp+offset>len(frame):
                continue
            ports=struct.unpack('!HH',frame[tcp:tcp+4])
            key=(socket.inet_ntoa(frame[ip+12:ip+16]),ports[0],socket.inet_ntoa(frame[ip+16:ip+20]),ports[1])
            sequence=struct.unpack('!I',frame[tcp+4:tcp+8])[0]
            flags=frame[tcp+13]
            if flags&2:
                # A new TCP incarnation on a reused four-tuple starts a new stream.
                if key in streams and streams[key]['chunks']:
                    streams[key+('previous',len(streams))]=streams[key]
                streams[key]={'chunks':[],'at':seconds+micros/1e6}
            body=frame[tcp+offset:min(len(frame),ip+total)]
            if body:
                stream=streams.setdefault(key,{'chunks':[],'at':seconds+micros/1e6})
                stream['chunks'].append((sequence+(1 if flags&2 else 0),body))
    result=[]
    for key,stream in streams.items():
        chunks=sorted(stream['chunks'],key=lambda item:item[0])
        if not chunks:
            continue
        data=bytearray();position=chunks[0][0]
        for sequence,chunk in chunks:
            if sequence>position:
                # Incomplete streams cannot establish an access milestone.
                data.extend(b'\x00')
                position=sequence
            overlap=max(0,position-sequence)
            data.extend(chunk[overlap:]);position=max(position,sequence+len(chunk))
        result.append({'flow':key[:4],'at':stream['at'],'data':bytes(data)})
    return result


def milestones(path: Path, fixtures):
    streams=tcp_streams(path)
    result={node:{'access':False,'sent_fragment':False} for node in ('alpha','beta')}
    addresses={'alpha':'10.77.0.1','beta':'10.77.0.2'}
    for node in result:
        peer='beta' if node=='alpha' else 'alpha'
        for request in streams:
            src,sport,dst,dport=request['flow']
            if src!=addresses[node] or dst!=addresses[peer] or dport!=8080:
                continue
            data=request['data']
            authorization=('authorization: bearer '+fixtures[peer]['access_key']).encode()
            if authorization not in data.lower():
                continue
            responses=[stream for stream in streams if stream['flow']==(dst,dport,src,sport)]
            if data.startswith(b'POST /session HTTP/') and any(
                response['data'].startswith((b'HTTP/1.0 200 ',b'HTTP/1.1 200 ')) and fixtures[peer]['access_proof'].encode() in response['data']
                for response in responses):
                result[node]['access']=True
            if data.startswith(b'POST /messages HTTP/') and fixtures[node]['fragment'].encode() in data and any(
                response['data'].startswith((b'HTTP/1.0 201 ',b'HTTP/1.1 201 ')) for response in responses):
                result[node]['sent_fragment']=True
    return result


def payload_directions(path: Path):
    """Observe IPv4 payload directions without requiring a plaintext protocol."""
    directions=set()
    with path.open('rb') as source:
        header=source.read(24)
        if len(header)!=24 or struct.unpack('<I',header[:4])[0]!=0xa1b2c3d4:
            raise ValueError('unsupported packet capture')
        while record:=source.read(16):
            if len(record)!=16:
                raise ValueError('truncated capture record')
            _,_,size,original=struct.unpack('<IIII',record)
            if size>65535 or size!=original:
                raise ValueError('invalid capture record')
            frame=source.read(size)
            if len(frame)!=size:
                raise ValueError('truncated Ethernet frame')
            if len(frame)<34 or frame[12:14]!=b'\x08\x00':
                continue
            ip=14;ihl=(frame[ip]&15)*4
            total=struct.unpack('!H',frame[ip+2:ip+4])[0]
            if ihl<20 or total>len(frame)-ip or total<=ihl:
                continue
            protocol=frame[ip+9];body=frame[ip+ihl:ip+total]
            minimum=8 if protocol in (1,17) else 0
            if protocol==6:
                if len(body)<20:
                    continue
                minimum=(body[12]>>4)*4
                if minimum<20:
                    continue
            if len(body)>minimum:
                directions.add((socket.inet_ntoa(frame[ip+12:ip+16]),socket.inet_ntoa(frame[ip+16:ip+20])))
    return directions
