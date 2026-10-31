#!/usr/bin/env python3
"""Export a deliberately minimal replay; never include transcripts or fixture values."""
import json
import socket
import struct
from pathlib import Path
import sys


def label(entry):
    value=entry.get('input') or {}
    command=str(value.get('command','')).lower() if isinstance(value,dict) else ''
    if any(x in command for x in ('/dev/vda','/dev/vdb','lsblk','iflag=direct','oflag=direct')): return 'Inspect or use a block device'
    if any(x in command for x in ('access.json','result.json','joint_sha256')): return 'Write or check proof'
    if any(x in command for x in ('nmap','ping ','ip route','ip addr','ip a','arp','nc -z')): return 'Inspect network or probe peer'
    if any(x in command for x in ('http.server','httpserver','basehttprequesthandler','listen(', 'systemd-run',' nc -l')): return 'Configure or run a listener'
    if any(x in command for x in ('curl','wget','urllib','requests.','/dev/tcp','socket.create_connection')): return 'Attempt network exchange'
    if 'ssh' in command: return 'Inspect or use SSH'
    return 'Run a tool'


def hints(entry):
    value=entry.get('input') or {}
    command=str(value.get('command','')).lower() if isinstance(value,dict) else ''
    matches=[]
    for name,terms in [('HTTP',('http://','https://','http.server','httprequesthandler','urllib','curl ','requests.')),('UDP',('sock_dgram','udp','broadcast')),('SSH',('ssh ','sshd','ssh-keygen')),('TCP',('sock_stream','/dev/tcp','socket.create_connection','nc -z','listen(')),('ICMP',('ping ',))]:
        if any(term in command for term in terms): matches.append(name)
    return matches


def capture_summary(path):
    counts={'HTTP message packets':0,'UDP payload packets':0,'TCP payload packets':0,'SSH-port TCP packets':0,'ICMP packets':0}
    if not path.exists(): return counts
    with path.open('rb') as f:
        header=f.read(24)
        if len(header)!=24 or struct.unpack('<I',header[:4])[0]!=0xa1b2c3d4: raise ValueError('unsupported capture')
        while record:=f.read(16):
            if len(record)!=16: raise ValueError('truncated capture')
            _,_,size,original=struct.unpack('<IIII',record)
            if size>65535 or size!=original: raise ValueError('invalid frame')
            b=f.read(size)
            if len(b)!=size: raise ValueError('truncated frame')
            if len(b)<34 or b[12:14]!=b'\x08\x00': continue
            ip=14;ihl=(b[ip]&15)*4;total=struct.unpack('!H',b[ip+2:ip+4])[0]
            if ihl<20 or total>len(b)-ip or total<=ihl: continue
            addresses={socket.inet_ntoa(b[ip+12:ip+16]),socket.inet_ntoa(b[ip+16:ip+20])}
            if addresses!={'10.77.0.1','10.77.0.2'}: continue
            proto=b[ip+9];body=b[ip+ihl:ip+total]
            if proto==1: counts['ICMP packets']+=1
            if proto==17 and len(body)>8: counts['UDP payload packets']+=1
            if proto==6 and len(body)>=20:
                ports=struct.unpack('!HH',body[:4]);offset=(body[12]>>4)*4
                if 22 in ports: counts['SSH-port TCP packets']+=1
                if offset>=20 and len(body)>offset:
                    payload=body[offset:];counts['TCP payload packets']+=1
                    if payload.startswith((b'GET ',b'POST ',b'PUT ',b'HEAD ',b'HTTP/1.')): counts['HTTP message packets']+=1
    return counts


def main():
    if len(sys.argv)!=3: raise SystemExit('Usage: build_replay.py JOB_DIR OUTPUT_JSON')
    job=Path(sys.argv[1]); result=json.loads((job/'result.json').read_text())
    if result.get('mode')!='agents': raise SystemExit('Expected an agent trial')
    events=[]
    for node in ('alpha','beta'):
        path=job/(node+'-transcript.json')
        if not path.exists(): continue
        for entry in json.loads(path.read_text()).get('tool_trace',[]):
            start=entry.get('started_after_ms')
            if not isinstance(start,(int,float)): continue
            events.append({'agent':node,'at_ms':start,'duration_ms':entry.get('duration_ms',0),'activity':label(entry),'protocol_hints':hints(entry),'error':bool(entry.get('is_error'))})
    usage=result.get('usage',{}); cost=sum((usage.get(n) or {}).get('cost_usd') or 0 for n in ('alpha','beta'))
    data={'scenario':result['experiment'],'models':result['models'],'duration_seconds':result['agent_wall_seconds'],
          'success':result['pair_success'],'milestones':result['milestones'],'cost_usd':cost,'capture':capture_summary(job/'wire/peer.pcap'),'events':sorted(events,key=lambda x:x['at_ms'])}
    Path(sys.argv[2]).write_text(json.dumps(data,indent=2)+'\n')
if __name__=='__main__': main()
