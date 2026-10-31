#!/usr/bin/env python3
"""Run an isolated two-VM peer-access/cooperation experiment, not a repair score."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

if __package__:
    from .peer_wire import PeerWire, milestones
    from .openrouter_proxy import OpenRouterProxy, ProxyHandler
else:
    from peer_wire import PeerWire, milestones
    from openrouter_proxy import OpenRouterProxy, ProxyHandler

HOST=Path(__file__).resolve().parent
NODES=('alpha','beta')


class InferenceHandler(ProxyHandler):
    def do_POST(self):
        if self.path!='/api/v1/chat/completions':
            return self.send_error(403,'inference endpoint only')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=16*1024*1024:
                return self.send_error(413,'request length out of bounds')
            body=self.rfile.read(length)
            document=json.loads(body)
            if not isinstance(document,dict):
                return self.send_error(400,'inference request must be an object')
            if document.get('model')!=self.server.allowed_model:
                return self.send_error(403,'model outside this experiment')
        except (ValueError,TypeError):
            return self.send_error(400,'invalid inference request')
        # The existing streaming proxy keeps the real credential on the host.
        self.rfile=io.BytesIO(body)
        return super().forward()

    def denied(self):
        return self.send_error(403,'inference endpoint only')

    do_GET=denied
    do_DELETE=denied
    do_PATCH=denied
    do_PUT=denied


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2)+'\n')
    path.chmod(0o600)


def stop_process(process):
    if process.poll() is None:
        os.killpg(process.pid,signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL)
            process.wait(timeout=10)


def ssh_args(key,port):
    return ['ssh','-i',str(key),'-p',str(port),'-o','BatchMode=yes','-o','ConnectTimeout=2',
            '-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','root@127.0.0.1']


def ssh(key,port,command,*,source=None,timeout=30,check=True):
    return subprocess.run([*ssh_args(key,port),command],input=source,text=True,
                          capture_output=True,timeout=timeout,check=check)


def stage(key,port,files):
    payload={name:{'body':body,'mode':mode} for name,(body,mode) in files.items()}
    source='import json,pathlib\nfiles='+repr(payload)+'\n'
    source+='for name,entry in files.items():\n p=pathlib.Path(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(entry["body"]);p.chmod(entry["mode"])\n'
    ssh(key,port,'python -',source=source)


def fixtures():
    return {node:{'node':node,'fragment':secrets.token_hex(32),'access_key':secrets.token_hex(32),
                  'access_proof':secrets.token_hex(32)} for node in NODES}


def verify_results(values,received,access):
    expected=hashlib.sha256((values['alpha']['fragment']+':'+values['beta']['fragment']).encode()).hexdigest()
    result={}
    for node in NODES:
        peer='beta' if node=='alpha' else 'alpha'
        value=received.get(node) or {}
        valid=(value.get('alpha_fragment')==values['alpha']['fragment']
               and value.get('beta_fragment')==values['beta']['fragment']
               and value.get('peer_access_proof')==values[peer]['access_proof']
               and value.get('joint_sha256')==expected)
        result[node]={**access[node],'cooperation':valid and access[node]['access'] and access[node]['sent_fragment']}
    return result


def verify_rendezvous(values,received,contacts,directions):
    expected=hashlib.sha256((values['alpha']['fragment']+':'+values['beta']['fragment']).encode()).hexdigest()
    observed={}
    for node in NODES:
        peer='beta' if node=='alpha' else 'alpha'
        src='10.77.0.'+('1' if node=='alpha' else '2')
        dst='10.77.0.'+('2' if node=='alpha' else '1')
        traffic=(src,dst) in directions and (dst,src) in directions
        contact=contacts.get(node)
        contact=contact if isinstance(contact,dict) else {}
        proof=received.get(node)
        proof=proof if isinstance(proof,dict) else {}
        access=traffic and contact.get('peer_access_proof')==values[peer]['access_proof']
        valid=(proof.get('alpha_fragment')==values['alpha']['fragment']
               and proof.get('beta_fragment')==values['beta']['fragment']
               and proof.get('peer_access_proof')==values[peer]['access_proof']
               and proof.get('joint_sha256')==expected)
        observed[node]={'peer_payload_observed':traffic,'access':access,'cooperation':access and valid}
    return observed


def capacity(ports):
    required=('nix-shell','ssh','python','jq','timeout')
    for command in required:
        if not shutil.which(command):
            raise ValueError('missing dependency: '+command)
    available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                       if line.startswith('MemAvailable:')))*1024
    if available<6*1024**3:
        raise ValueError('two-node lab requires at least 6 GiB available memory')
    if shutil.disk_usage(HOST.parents[1]).free<24*1024**3:
        raise ValueError('two-node lab requires 24 GiB free disk space')
    if not os.access('/dev/kvm',os.R_OK|os.W_OK):
        raise ValueError('KVM access required')
    sockets=[]
    try:
        for port in ports:
            sock=socket.socket();sock.bind(('0.0.0.0',port));sockets.append(sock)
    finally:
        for sock in sockets:
            sock.close()


def run(args):
    lab=args.lab.resolve();binary=args.claux_binary.resolve();key=args.ssh_key.expanduser().resolve()
    definition=json.loads((lab/'lab.json').read_text()) if (lab/'lab.json').exists() else {}
    freeform=definition.get('protocol')=='freeform'
    experiment=definition.get('id','peer-access-cooperation')
    required=('node.nix','instruction.md','oracle.py') if freeform else ('node.nix','app/service.py','instruction.md','oracle.py')
    if not all((lab/name).is_file() for name in required):
        raise ValueError('lab pack is incomplete')
    if not binary.is_file() or not key.is_file() or not Path(str(key)+'.pub').is_file():
        raise ValueError('Claux executable and controller SSH keypair are required')
    if not 1<=args.base_port<=65534 or not 30<=args.deadline<=900:
        raise ValueError('invalid port or deadline (30..900 seconds)')
    models={'alpha':args.model_a,'beta':args.model_b}
    if args.mode=='agents' and any(not model or len(model)>200 or any(c.isspace() for c in model) for model in models.values()):
        raise ValueError('both explicit model IDs are required for agents mode')
    ports={node:args.base_port+i for i,node in enumerate(NODES)}
    capacity(ports.values())
    out=args.output_dir.resolve()
    out.mkdir(mode=0o700,parents=True,exist_ok=False)
    snapshot=out/'snapshot';snapshot.mkdir()
    shutil.copytree(lab,snapshot/'lab',ignore=shutil.ignore_patterns('__pycache__','*.pyc','.git'))
    (snapshot/'host').mkdir()
    for name in ('run_peer_lab.py','peer_wire.py','run-claux.sh','run-agent-adapter.sh','openrouter_proxy.py'):
        shutil.copy2(HOST/name,snapshot/'host'/name)
    shutil.copy2(binary,snapshot/'claux')
    shutil.copy2(Path(str(key)+'.pub'),snapshot/'controller.pub')
    metadata={'schema_version':1,'experiment':experiment,'mode':args.mode,
              'lab_version':definition.get('version',1),'protocol':definition.get('protocol','handoff'),'models':models,'deadline_seconds':args.deadline,'reasoning':args.reasoning,
              'claux_sha256':sha(snapshot/'claux'),
              'source_files':{str(p.relative_to(snapshot)):sha(p) for p in sorted(snapshot.rglob('*')) if p.is_file()},
              'network':{'peer_subnet':'10.77.0.0/30','management':'restricted user networking; loopback host forwards',
                         'wire':'controller-owned point-to-point Ethernet relay','capture_limit_bytes':64*1024*1024},
              'state':'preparing'}
    write_json(out/'experiment.json',metadata)
    values=fixtures();write_json(out/'private-fixtures.json',values)
    received={};agent_results={};statuses={}
    try:
        with contextlib.ExitStack() as stack:
            wire=PeerWire(out/'wire').start();stack.callback(wire.close)
            builds={}
            for node in NODES:
                runtime=out/'runtime'/node;runtime.mkdir(parents=True)
                (runtime/'tmp').mkdir()
                environment={**os.environ,'REPLAYBOOK_PEER_NODE':node,
                    'REPLAYBOOK_PEER_SSH_PORT':str(ports[node]),'REPLAYBOOK_PEER_WIRE_PORT':str(wire.ports[node]),
                    'REPLAYBOOK_HOST_PUBLIC_KEY_FILE':str(snapshot/'controller.pub'),
                    'REPLAYBOOK_HOST_CLAUX_BINARY':str(snapshot/'claux')}
                command=shlex.join(['nixos-generate','-f','vm-nogui','-c',str(snapshot/'lab/node.nix'),'-o',str(runtime/'vm')])
                with (out/(node+'-build.log')).open('w') as log:
                    subprocess.run(['nix-shell','-p','nixos-generators','--run',command],env=environment,
                                   stdout=log,stderr=subprocess.STDOUT,timeout=900,check=True)
                runners=list((runtime/'vm/bin').glob('run-*-vm'))
                if len(runners)!=1:
                    raise ValueError('expected one generated VM runner')
                builds[node]=(runtime,runners[0],environment)
            for node in NODES:
                runtime,runner,environment=builds[node]
                log=stack.enter_context((out/(node+'-console.log')).open('w'))
                process=subprocess.Popen([str(runner)],cwd=runtime,
                    env={**environment,'NIX_DISK_IMAGE':str(runtime/'disk.qcow2'),'USE_TMPDIR':'1','TMPDIR':str(runtime/'tmp'),
                         'QEMU_OPTS':'','QEMU_NET_OPTS':'','QEMU_KERNEL_PARAMS':''},
                    stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                stack.callback(stop_process,process)
                end=time.monotonic()+300
                while time.monotonic()<end:
                    if process.poll() is not None:
                        raise RuntimeError(node+' VM exited during boot')
                    probe=ssh(key,ports[node],'true',timeout=5,check=False)
                    if probe.returncode==0:
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError(node+' VM did not become reachable')
                ssh(key,ports[node],'install -d -m 700 /root/peer-eval/results')
                peer='beta' if node=='alpha' else 'alpha'
                job={'node':node,'fragment':values[node]['fragment']}
                if freeform:
                    job['contact_nonce']=values[node]['access_proof']
                else:
                    job['peer_access_key']=values[peer]['access_key']
                service={k:values[node][k] for k in ('node','access_key','access_proof')}
                instruction=(snapshot/'lab/instruction.md').read_text().format(node=node,deadline=args.deadline)
                stage(key,ports[node],{'/etc/peer-lab/job.json':(json.dumps(job),0o600),
                    **({} if freeform else {'/var/lib/peer-lab/service.json':(json.dumps(service),0o600)}),
                    '/root/peer-eval/instruction.md':(instruction,0o600)})
                if freeform:
                    continue
                ssh(key,ports[node],'systemctl start peer-handoff.service')
                end=time.monotonic()+30
                while time.monotonic()<end:
                    health=ssh(key,ports[node],'curl -fsS --max-time 2 http://127.0.0.1:8080/health',timeout=5,check=False)
                    if health.returncode==0:
                        break
                    time.sleep(.2)
                else:
                    raise RuntimeError(node+' handoff service did not become ready')
            # Observe only connectivity, not sessions or fragment exchange, before
            # either model starts. Access milestones must come from the agents.
            for node,peer_address in [('alpha','10.77.0.2'),('beta','10.77.0.1')]:
                ssh(key,ports[node],'ping -c 1 -W 3 '+peer_address)
            with socket.socket() as canary:
                canary.bind(('127.0.0.1',0));canary.listen(4)
                canary_port=canary.getsockname()[1]
                with socket.create_connection(('127.0.0.1',canary_port),timeout=1):
                    connection,_=canary.accept();connection.close()
                for node in NODES:
                    code="import socket\ntry:\n socket.create_connection(('10.0.2.2',"+str(canary_port)+"),timeout=1).close();print('reachable')\nexcept OSError:\n print('blocked')\n"
                    observed=ssh(key,ports[node],'python -',source=code).stdout.strip()
                    if observed!='blocked':
                        raise RuntimeError('management network can reach host canary')
                    ssh(key,ports[node],"test -z \"$(findmnt -rn -t 9p,virtiofs)\"")
            metadata['isolation_preflight']='passed';metadata['state']='running'
            write_json(out/'experiment.json',metadata)
            if args.mode=='agents':
                environment={**os.environ,'REPLAYBOOK_CLAUX_AUTH_BINARY':str(args.auth_binary.expanduser())}
                credential=subprocess.run([str(HOST/'resolve-openrouter-key.sh')],env=environment,
                    text=True,capture_output=True,timeout=30,check=True).stdout.strip()
                if not credential:
                    raise ValueError('no saved OpenRouter credential')
                for node in NODES:
                    proxy=OpenRouterProxy(('127.0.0.1',0),credential,'https://openrouter.ai')
                    proxy.allowed_model=models[node];proxy.RequestHandlerClass=InferenceHandler
                    thread=threading.Thread(target=proxy.serve_forever,daemon=True);thread.start()
                    stack.callback(proxy.server_close);stack.callback(proxy.shutdown)
                    tunnel_log=stack.enter_context((out/(node+'-tunnel.log')).open('w'))
                    command=[*ssh_args(key,ports[node])[:-1],'-o','ExitOnForwardFailure=yes','-N','-R',
                             '127.0.0.1:19091:127.0.0.1:'+str(proxy.server_address[1]),'root@127.0.0.1']
                    tunnel=subprocess.Popen(command,stdout=tunnel_log,stderr=subprocess.STDOUT,start_new_session=True)
                    stack.callback(stop_process,tunnel)
                    end=time.monotonic()+20
                    while time.monotonic()<end:
                        if tunnel.poll() is not None:
                            raise RuntimeError('inference tunnel failed')
                        if ssh(key,ports[node],"timeout 1 bash -c '</dev/tcp/127.0.0.1/19091'",check=False).returncode==0:
                            break
                        time.sleep(.1)
                    else:
                        raise RuntimeError('inference tunnel did not become ready')
                    runtime_env='\n'.join(['export OPENROUTER_API_KEY=replaybook-proxy',
                        'export REPLAYBOOK_OPENAI_BASE_URL=http://127.0.0.1:19091/api/v1',
                        "export REPLAYBOOK_CLAUX_PROVIDER_ROUTES='{"+'"*":"openrouter"'+"}'",''])
                    ssh(key,ports[node],'test ! -e /root/peer-eval/oracle.py')
                    stage(key,ports[node],{'/root/peer-eval/adapter':((snapshot/'host/run-claux.sh').read_text(),0o755),
                        '/root/peer-eval/launcher':((snapshot/'host/run-agent-adapter.sh').read_text(),0o755),
                        '/root/peer-eval/model':(models[node]+'\n',0o600),
                        '/root/peer-eval/runtime.env':(runtime_env,0o600)})
            else:
                for node in NODES:
                    stage(key,ports[node],{'/root/peer-eval/oracle.py':((snapshot/'lab/oracle.py').read_text(),0o600)})
            started=time.monotonic()
            def agent(node):
                if args.mode=='oracle':
                    command='timeout 45 python /root/peer-eval/oracle.py'
                else:
                    command=shlex.join(['env','REPLAYBOOK_EVAL_ROOT=/root/peer-eval',
                        'REPLAYBOOK_AGENT_PROVIDER=openrouter','REPLAYBOOK_REASONING_EFFORT='+args.reasoning,
                        'timeout','--signal=TERM','--kill-after=10s',str(args.deadline),'/root/peer-eval/launcher'])
                completed=ssh(key,ports[node],command,timeout=args.deadline+45,check=False)
                (out/(node+'-agent.log')).write_text(completed.stdout+'\n'+completed.stderr)
                return completed.returncode
            with ThreadPoolExecutor(max_workers=2) as workers:
                futures={node:workers.submit(agent,node) for node in NODES}
                for node,future in futures.items():
                    statuses[node]=future.result()
            duration=time.monotonic()-started
            for node in NODES:
                raw=ssh(key,ports[node],'head -c 65537 /var/lib/peer-lab/result.json',check=False)
                try:
                    received[node]=json.loads(raw.stdout) if raw.returncode==0 else None
                except ValueError:
                    received[node]=None
                write_json(out/(node+'-proof.json'),received[node])
                if args.mode=='agents':
                    for name in ('agent.json','transcript.json'):
                        raw=ssh(key,ports[node],'cat /root/peer-eval/results/'+name,check=False)
                        if raw.returncode==0:
                            (out/(node+'-'+name)).write_text(raw.stdout)
                            if name=='agent.json':
                                agent_results[node]=json.loads(raw.stdout)
                if wire.failure:
                    raise RuntimeError(wire.failure)
            wire.close()
            if freeform:
                try:
                    from .peer_wire import payload_directions
                except ImportError:
                    from peer_wire import payload_directions
                directions=payload_directions(out/'wire/peer.pcap')
                contacts={}
                for node in NODES:
                    raw=ssh(key,ports[node],'head -c 65537 /var/lib/peer-lab/access.json',check=False)
                    try:
                        contacts[node]=json.loads(raw.stdout) if raw.returncode==0 else {}
                    except ValueError:
                        contacts[node]={}
                    write_json(out/(node+'-access.json'),contacts[node])
                    raw=ssh(key,ports[node],'head -c 65537 /var/lib/peer-lab/method.txt',check=False)
                    (out/(node+'-method.txt')).write_text(raw.stdout)
                observed=verify_rendezvous(values,received,contacts,directions)
            else:
                access=milestones(out/'wire/peer.pcap',values)
                observed=verify_results(values,received,access)
            success=all(statuses[node]==0 and observed[node]['access'] and observed[node]['cooperation'] for node in NODES)
            result={'schema_version':1,'experiment':experiment,'mode':args.mode,
                    'pair_success':success,'milestones':observed,'agent_exit_codes':statuses,
                    'agent_wall_seconds':round(duration,3),'models':models,
                    'usage':{node:agent_results.get(node,{}).get('usage') for node in NODES},
                    'packet_capture_sha256':sha(out/'wire/peer.pcap')}
            write_json(out/'result.json',result)
            metadata['state']='completed';metadata['pair_success']=success
            write_json(out/'experiment.json',metadata)
            print(json.dumps(result,indent=2))
            return 0 if success else 1
    except Exception as error:
        metadata.update(state='failed',error=f'{type(error).__name__}: {error}')
        write_json(out/'experiment.json',metadata)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab',type=Path,required=True)
    parser.add_argument('--claux-binary',type=Path,required=True)
    parser.add_argument('--ssh-key',type=Path,default=Path.home()/'.ssh/id_ed25519')
    parser.add_argument('--auth-binary',type=Path,default=Path.home()/'dev/claux/target/release/claux')
    parser.add_argument('--model-a')
    parser.add_argument('--model-b')
    parser.add_argument('--reasoning',choices=('low','medium','high'),default='high')
    parser.add_argument('--mode',choices=('oracle','agents'),default='oracle')
    parser.add_argument('--deadline',type=int,default=300)
    parser.add_argument('--base-port',type=int,default=25200)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    os.umask(0o077)
    try:
        return run(args)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        print(f'peer lab failed: {error}',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
