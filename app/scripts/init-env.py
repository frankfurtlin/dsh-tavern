"""Create private deployment secrets once; never overwrite an existing .env."""
import argparse
import base64
import secrets
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('--real',action='store_true',help='Use real providers instead of deterministic mock responses')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
target=root/'.env'
if target.exists():raise SystemExit('.env already exists; nothing changed')
value=(root/'.env.example').read_text('utf8')
for key,replacement in {
    'POSTGRES_PASSWORD':secrets.token_urlsafe(24),
    'JWT_SIGNING_SECRET':secrets.token_urlsafe(48),
    'PROVIDER_MASTER_KEY':base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    'AI_MODE':'real' if args.real else 'mock',
}.items():
    lines=value.splitlines();value='\n'.join(key+'='+replacement if line.startswith(key+'=') else line for line in lines)+'\n'
with target.open('x',encoding='utf8') as out:out.write(value)
try:target.chmod(0o600)
except OSError:pass
print('Created private .env; mode='+('real' if args.real else 'mock')+'. No secrets printed.')
