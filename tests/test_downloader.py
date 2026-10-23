import hashlib
import io
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/".build-env"))
from launcher.core import Cancelled, LauncherError, Network, Task


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {"source":"official","concurrency":3,"download_segments":4,"download_parallel":True}
        self.network = Network(lambda:self.config)
        self.payload = bytes(range(256))*(32768)  # 8 MiB, above the real threshold.
        self.lock = threading.Lock()
        self.active, self.peak, self.requests = 0,0,[]

    def tearDown(self): self.temp.cleanup()

    def opener(self,mode="valid",task=None):
        owner = self
        class Response(io.BytesIO):
            def __enter__(self):
                with owner.lock:
                    owner.active+=1
                    owner.peak=max(owner.peak,owner.active)
                return self
            def __exit__(self,*args):
                with owner.lock: owner.active-=1
                self.close()
            def geturl(self): return "https://example.com/file"
            def read(self,n=-1):
                if self.status==206:
                    time.sleep(.004)
                    if task and mode=="cancel": task.cancel.set()
                return super().read(n)
        def open(request,**kwargs):
            part = request.get_header("Range")
            with owner.lock: owner.requests.append(part)
            payload = owner.payload
            status = 200
            headers={"Content-Length":str(len(payload)),"ETag":"\"v1\""}
            if mode!="plain": headers["Accept-Ranges"]="bytes"
            if part and mode not in ("ignore","plain"):
                start,end=map(int,part.partition("=")[2].split("-"))
                payload=payload[start:end+1]
                if mode=="corrupt": payload=b"x"*len(payload)
                status=206
                headers.update({"Content-Length":str(len(payload)),"Content-Range":"bytes {}-{}/{}".format(start+(1 if mode=="bad-range" else 0),end,len(owner.payload))})
            response=Response(payload)
            response.status,response.headers=status,headers
            return response
        return open

    def download(self,mode="valid",task=None):
        target=self.root/"result.jar"
        with patch("urllib.request.urlopen",side_effect=self.opener(mode,task)),patch("launcher.core.time.sleep"):
            self.network.download("https://example.com/file",target,sha1=hashlib.sha1(self.payload).hexdigest(),size=len(self.payload),task=task)
        return target

    def test_ranges_are_concurrent_bounded_and_hash_verified(self):
        task=Task("Range",None)
        task.status="running"
        target=self.download(task=task)
        self.assertEqual(target.read_bytes(),self.payload)
        self.assertEqual(sum(bool(x) for x in self.requests),4)
        self.assertGreaterEqual(self.peak,2)
        self.assertLessEqual(self.peak,3)
        self.assertEqual(task.bytes,len(self.payload))
        self.assertEqual(self.network.engine.active,0)

    def test_plain_and_advertised_but_ignored_ranges_fall_back_safely(self):
        for mode in ("plain","ignore","bad-range"):
            with self.subTest(mode=mode):
                target=self.download(mode)
                self.assertEqual(target.read_bytes(),self.payload)
                target.unlink()
                self.assertEqual(self.active,0)

    def test_cancel_and_corruption_leave_no_partial_or_damaged_target(self):
        task=Task("Cancel",None)
        with self.assertRaises(Cancelled): self.download("cancel",task)
        self.assertEqual(list(self.root.glob("*.part-*")),[])
        target=self.root/"result.jar"
        target.write_bytes(b"old target")
        with self.assertRaises(LauncherError): self.download("corrupt")
        self.assertEqual(target.read_bytes(),b"old target")
        self.assertEqual(list(self.root.glob("*.part-*")),[])
        self.assertEqual(self.network.engine.active,0)

    def test_connection_limit_is_shared_across_parallel_files(self):
        self.config["concurrency"]=2
        with patch("urllib.request.urlopen",side_effect=self.opener()):
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs=[pool.submit(self.network.download,"https://example.com/file",self.root/(str(i)+".jar"),sha1=hashlib.sha1(self.payload).hexdigest()) for i in range(2)]
                for job in jobs: job.result(timeout=8)
        self.assertEqual(self.peak,2)
        self.assertEqual(self.active,0)

    def test_real_https_range_transport(self):
        import datetime, ipaddress, ssl
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME,"127.0.0.1")])
        now=datetime.datetime.utcnow()
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
              .not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),False).sign(key,hashes.SHA256()))
        pem=self.root/"test-cert.pem"
        private=self.root/"test-key.pem"
        pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        owner=self
        ranges=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                data=owner.payload
                value=self.headers.get("Range")
                if value:
                    start,end=map(int,value.partition("=")[2].split("-"))
                    ranges.append(value)
                    data=data[start:end+1]
                    self.send_response(206)
                    self.send_header("Content-Range","bytes {}-{}/{}".format(start,end,len(owner.payload)))
                else: self.send_response(200)
                self.send_header("Content-Length",str(len(data)))
                self.send_header("Accept-Ranges","bytes")
                self.send_header("ETag",'"local-v1"')
                self.end_headers()
                try:
                    if value: time.sleep(.03)
                    self.wfile.write(data)
                except OSError: pass
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        server.daemon_threads=True
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(pem),str(private))
        server.socket=context.wrap_socket(server.socket,server_side=True)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with patch("launcher.downloader.SSL_CONTEXT",ssl.create_default_context(cafile=str(pem))):
                target=self.root/"tls.jar"
                self.network.download("https://127.0.0.1:{}/file".format(server.server_port),target,sha1=hashlib.sha1(self.payload).hexdigest())
            self.assertEqual(target.read_bytes(),self.payload)
            self.assertEqual(len(ranges),4)
        finally:
            server.shutdown()
            server.server_close()
