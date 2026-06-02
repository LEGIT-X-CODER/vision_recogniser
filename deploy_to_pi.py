import paramiko
import os
import scp
from tqdm import tqdm

# Configuration
PI_IP = "192.168.137.245"
PI_USER = "pi"
PI_PASS = "asdfghjkl;'"
REMOTE_DIR = "/home/pi/vision_recogniser"

def create_ssh_client():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(PI_IP, username=PI_USER, password=PI_PASS)
    return client

def deploy():
    print(f"Connecting to {PI_IP}...")
    ssh = create_ssh_client()
    
    print(f"Creating directory {REMOTE_DIR}...")
    ssh.exec_command(f"mkdir -p {REMOTE_DIR}")
    
    print("Uploading files...")
    with scp.SCPClient(ssh.get_transport()) as scp_client:
        files_to_upload = [
            "capture.py", "config.py", "firebase_upload.py", "gemini_api.py",
            "heartbeat.py", "main.py", "requirements.txt", "sensor.py", "test_all.py"
        ]
        for f in files_to_upload:
            if os.path.exists(f):
                print(f"  Uploading {f}...")
                scp_client.put(f, os.path.join(REMOTE_DIR, f))
            else:
                print(f"  Warning: {f} not found locally.")
    
    print("Files uploaded successfully.")
    ssh.close()

if __name__ == "__main__":
    deploy()
