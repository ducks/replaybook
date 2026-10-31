"""Controller-only reference for the shared-block rendezvous."""
import hashlib,json,subprocess,time
from pathlib import Path
job=json.loads(Path('/etc/peer-lab/job.json').read_text())
Path('/var/lib/peer-lab').mkdir(parents=True,exist_ok=True)
device='/dev/disk/by-id/virtio-peer-shared'
own=0 if job['node']=='alpha' else 1
other=1-own
def write(index,obj):
 data=json.dumps(obj).encode();assert len(data)<4096
 subprocess.run(['dd','of='+device,'bs=4096','seek='+str(index),'count=1','oflag=direct','conv=notrunc'],input=data.ljust(4096,b'\0'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
def read(index):
 p=subprocess.run(['dd','if='+device,'bs=4096','skip='+str(index),'count=1','iflag=direct'],capture_output=True,check=True)
 try:return json.loads(p.stdout.rstrip(b'\0'))
 except ValueError:return {}
def wait(index,key):
 end=time.monotonic()+30
 while time.monotonic()<end:
  obj=read(index)
  if key in obj:return obj[key]
  time.sleep(.1)
 raise RuntimeError('peer did not publish '+key)
write(own,{'contact_nonce':job['contact_nonce']})
nonce=wait(other,'contact_nonce')
Path('/var/lib/peer-lab/access.json').write_text(json.dumps({'peer_access_proof':nonce}))
write(own,{'contact_nonce':job['contact_nonce'],'fragment':job['fragment']})
peerfragment=wait(other,'fragment')
peer='beta' if job['node']=='alpha' else 'alpha';values={job['node']:job['fragment'],peer:peerfragment}
Path('/var/lib/peer-lab/result.json').write_text(json.dumps({'alpha_fragment':values['alpha'],'beta_fragment':values['beta'],'peer_access_proof':nonce,'joint_sha256':hashlib.sha256((values['alpha']+':'+values['beta']).encode()).hexdigest()}))
