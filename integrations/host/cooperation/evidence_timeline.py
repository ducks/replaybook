#!/usr/bin/env python3
"""Create a minimized evidence timeline from a completed local trial."""
import hashlib
import json
from pathlib import Path
import socket
import struct
import sys

from build_replay import label, hints


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def network_events(path):
    seen=set();events=[];first=None
    with path.open('rb') as f:
        header=f.read(24)
        if len(header)!=24 or struct.unpack('<I',header[:4])[0]!=0xa1b2c3d4: raise ValueError('unsupported capture')
        while record:=f.read(16):
            if len(record)!=16: raise ValueError('truncated capture')
            sec,micro,size,original=struct.unpack('<IIII',record)
            if size>65535 or size!=original: raise ValueError('invalid frame')
            packet=f.read(size)
            if len(packet)!=size: raise ValueError('truncated frame')
            stamp=sec+micro/1e6
            if first is None:first=stamp
            if len(packet)<34 or packet[12:14]!=b'\x08\x00':continue
            ip=14;ihl=(packet[ip]&15)*4;total=struct.unpack('!H',packet[ip+2:ip+4])[0]
            if ihl<20 or total>len(packet)-ip or total<=ihl:continue
            src=socket.inet_ntoa(packet[ip+12:ip+16]);dst=socket.inet_ntoa(packet[ip+16:ip+20])
            if {src,dst}!={'10.77.0.1','10.77.0.2'}:continue
            payload=packet[ip+ihl:ip+total];protocol=packet[ip+9];kind=None;port=None
            if protocol==1 and len(payload)>8:kind='ICMP packet'
            elif protocol==17 and len(payload)>8:kind='UDP payload'
            elif protocol==6 and len(payload)>=20:
                ports=struct.unpack('!HH',payload[:4]);offset=(payload[12]>>4)*4
                if offset<20 or len(payload)<=offset:continue
                content=payload[offset:]
                if content.startswith((b'GET ',b'POST ',b'PUT ',b'HEAD ')):kind='Plaintext HTTP request'
                elif content.startswith(b'HTTP/1.'):kind='Plaintext HTTP response'
                elif 22 in ports:kind='TCP payload on port 22'
                else:kind='TCP payload'
                port=ports[1] if ports[1] in (22,80,443,8765,8000,8080,9999) else None
            if kind is None:continue
            actor='alpha' if src=='10.77.0.1' else 'beta'
            key=(actor,kind)
            if key in seen:continue
            seen.add(key)
            event={'source':'packet capture','clock':'capture-relative','at_ms':round((stamp-first)*1000),'actor':actor,'event':kind,'meaning':'First observed packet in this direction; not proof of application success.'}
            if port is not None:event['destination_port']=port
            events.append(event)
    return events


def export(job):
    result=json.loads((job/'result.json').read_text())
    if result.get('mode')!='agents':raise ValueError('expected a completed agent trial')
    files={'result.json':digest(job/'result.json'),'wire/peer.pcap':digest(job/'wire/peer.pcap')}
    if files['wire/peer.pcap']!=result['packet_capture_sha256']:raise ValueError('capture digest mismatch')
    agent_events=[]
    for node in ('alpha','beta'):
        path=job/(node+'-transcript.json')
        if not path.exists():continue
        files[path.name]=digest(path)
        transcript=json.loads(path.read_text())
        for entry in transcript.get('tool_trace',[]):
            start=entry.get('started_after_ms')
            if not isinstance(start,(int,float)):continue
            agent_events.append({'source':'tool trace','clock':node+'-relative','at_ms':start,'actor':node,'event':label(entry),'protocol_hints':hints(entry),'tool_error':bool(entry.get('is_error')),'meaning':'Tool input classification; an attempt, not evidence it succeeded.'})
    checks=[]
    for node in ('alpha','beta'):
        value=result['milestones'][node]
        for key,title in (('access','Peer nonce in contact receipt'),('cooperation','Joint proof verified')):
            checks.append({'source':'controller result','clock':'untimed','actor':node,'event':title,'confirmed':bool(value[key]),'meaning':'Final outcome; time of the underlying action is not recorded.'})
    return {'schema_version':1,'scenario':result['experiment'],'clock_note':'Agent offsets have independent start times; packet capture starts before agent execution. Do not compare timestamps across clocks.','evidence_sha256':files,'agent_events':sorted(agent_events,key=lambda e:(e['actor'],e['at_ms'])),'network_events':network_events(job/'wire/peer.pcap'),'controller_checks':checks}


def main():
    if len(sys.argv)!=3:raise SystemExit('Usage: evidence_timeline.py JOB_DIR OUTPUT_JSON')
    Path(sys.argv[2]).write_text(json.dumps(export(Path(sys.argv[1])),indent=2)+'\n')
if __name__=='__main__':main()
