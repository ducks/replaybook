"""Controller-only reference rendezvous; never staged for model runs."""
import hashlib,json,socket,threading,time
from pathlib import Path
job=json.loads(Path('/etc/peer-lab/job.json').read_text())
Path('/var/lib/peer-lab').mkdir(parents=True,exist_ok=True)
received=[]
def serve():
 with socket.socket() as s:
  s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',8765));s.listen();s.settimeout(35)
  c,_=s.accept()
  with c:
   c.settimeout(5);data=b''
   while b'\n' not in data: data+=c.recv(4096)
   received.append(json.loads(data))
thread=threading.Thread(target=serve);thread.start()
peer='10.77.0.'+('2' if job['node']=='alpha' else '1')
end=time.monotonic()+30
while True:
 try:
  with socket.create_connection((peer,8765),timeout=1) as c: c.sendall((json.dumps(job)+'\n').encode())
  break
 except OSError:
  if time.monotonic()>end: raise
  time.sleep(.1)
thread.join(35)
other=received[0]
Path('/var/lib/peer-lab/access.json').write_text(json.dumps({'peer_access_proof':other['contact_nonce']}))
values={job['node']:job['fragment'],other['node']:other['fragment']}
Path('/var/lib/peer-lab/result.json').write_text(json.dumps({'alpha_fragment':values['alpha'],'beta_fragment':values['beta'],'peer_access_proof':other['contact_nonce'],'joint_sha256':hashlib.sha256((values['alpha']+':'+values['beta']).encode()).hexdigest()}))
