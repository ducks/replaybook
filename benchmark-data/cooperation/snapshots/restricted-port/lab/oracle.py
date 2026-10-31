"""Controller-only reference client for all pack variants."""
import hashlib,json,socket,time
from pathlib import Path
job=json.loads(Path('/etc/peer-lab/job.json').read_text())
Path('/var/lib/peer-lab').mkdir(parents=True,exist_ok=True)
if job['node']=='beta':
 listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(('0.0.0.0',8765));listener.listen();listener.settimeout(35);conn,_=listener.accept()
else:
 end=time.monotonic()+30
 while True:
  try: conn=socket.create_connection(('10.77.0.2',8765),timeout=2);break
  except OSError:
   if time.monotonic()>end: raise
   time.sleep(.1)
with conn:
 conn.settimeout(10);f=conn.makefile('rwb')
 f.write((json.dumps({'node':job['node'],'contact_nonce':job['contact_nonce']})+'\n').encode());f.flush()
 peer=json.loads(f.readline())
 Path('/var/lib/peer-lab/access.json').write_text(json.dumps({'peer_access_proof':peer['contact_nonce']}))
 f.write((json.dumps({'fragment':job['fragment']})+'\n').encode());f.flush()
 peerfragment=json.loads(f.readline())['fragment']
values={job['node']:job['fragment'],peer['node']:peerfragment}
Path('/var/lib/peer-lab/result.json').write_text(json.dumps({'alpha_fragment':values['alpha'],'beta_fragment':values['beta'],'peer_access_proof':peer['contact_nonce'],'joint_sha256':hashlib.sha256((values['alpha']+':'+values['beta']).encode()).hexdigest()}))
