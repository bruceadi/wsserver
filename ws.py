import zmq
import hashlib
import base64
import os
import struct

def make_http_response(http_version,  fn):
   if not os.path.exists(fn):
     not_found_page = """<head>
<title>Error response</title>
</head>
<body>
<h1>Error response</h1>
<p>Error code 404.
<p>Message: File not found.
<p>Error code explanation: 404 = Nothing matches the given URI.
</body>"""
     rsp ="\r\n".join(["{0} 404 File not found".format(http_version), "Content-Type: text/html",  "Content-Length:{0}".format(len(not_found_page)), "\r\n"])
     return rsp  + not_found_page     
   else:
     with open(fn, "rb") as fp:
        c = fp.read()
     content_type = "text/plain"
     if fn.endswith(".html") :
        content_type = "text/html"
     elif fn.endswith(".js"):
        content_type = "application/javascript"
     elif fn.endswith(".css"):
       content_type = "text/css"
     elif fn.endswith(".txt"):
       content_type = "text/plain"
     elif fn.endswith(".log"):
       content_type = "text/plain"
     rsp = "\r\n".join(["{0} 200 OK".format(http_version),"Content-Length:{0}".format(len(c)),"Content-type:{content_type}".format(content_type=content_type), "\r\n"])   
     return rsp + c


handshake = '\
HTTP/1.1 101 Switching Protocols\r\n\
Upgrade: websocket\r\n\
Connection: Upgrade\r\n\
Sec-WebSocket-Accept: {0}\r\n\r\n\
'
def session():
  handshaked = False
  data = ''
  while not handshaked:       
      if data.find('\r\n\r\n') != -1:
          hdr, rest = data.split('\r\n\r\n', 1)
          if rest: data =rest
          else: data = ''
          hdr_map = {}
          for item in hdr.split('\r\n'):
              if ": " in item:
                k,v = item.split(": ")
                hdr_map[k] = v
              else:
                cmd,fn,http_version= iter(item.split())
                if cmd == "GET" and  fn != "/":
                  tmp = yield "http", make_http_response(http_version, fn[1:])
          if "Sec-WebSocket-Key" in hdr_map:           
            Sec_WebSocket_Key = hdr_map["Sec-WebSocket-Key"]
            hash_value = hashlib.sha1(Sec_WebSocket_Key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()
            Sec_WebSocket_Accept = base64.b64encode(hash_value)
            handshaked = True
            tmp = yield "websocket", handshake.format(Sec_WebSocket_Accept)
      else: tmp = yield None, None
      data = data + tmp

  msg = ''
  while 1:
    while len(data) < 2:
       tmp = yield  None  
    if tmp: data = data + tmp
    opcode = ord(data[0]) & 0xf
    fin = (ord(data[0]) & 0x80) == 0x80
    mask_flag = (ord(data[1]) & 0x80) == 0x80
    l =  ord(data[1]) & 0x7f
    off = 2
    if l == 126: 
      while len(data) < 4:
         tmp = yield  None
         if tmp: data = data + tmp
      l, = struct.unpack(">H", data[2:4]) 
      off = 4
    elif l == 127: 
      while len(data) < 10:
         tmp = yield  None
         if tmp: data = data + tmp
      l, = struct.unpack(">Q", data[2:10]) 
      off = 10 

    required_len = l + 2 + ( 4 if mask_flag else 0)
    while len(data) < required_len:
      tmp = yield  None
      if tmp: data = data + tmp

    if mask_flag:
        mask = data[off:off + 4]
        received = []
        for i,ch in enumerate(data[off + 4:off + 4 + l]):
          m = mask[i % 4]
          v = ord(ch) ^ ord(m)
          received.append(chr(v))
        data = data[off + 4 + l:]
    else: 
      received = data[off : off + l]
      data = data[off+l:]
    msg = msg + ''.join(received)
    if fin: 
      if opcode != 0x8:
        tmp = yield msg 
      else: tmp = yield None

      if tmp: data = data + tmp
      msg = '' 
 
class Server:
   def __init__(self, ip, port):

     self.ctx = zmq.Context()   
     self.sock = zmq.Socket(self.ctx,zmq.STREAM)
     self.sock.bind("tcp://{ip}:{port}".format(ip=ip, port = port)) 
     self.clients = {}
     self.websockets = set()
        
   def received(self, id_frame, data_frame):
    if not data_frame:        
      if id_frame in self.clients:
        del self.clients[id_frame]
        if id_frame in self.websockets: self.websockets.remove(id_frame)        
      else:
        gen = session()
        self.clients[id_frame] = gen
        gen.send(None)
    else:
      if id_frame in self.websockets: #already in ready websocket 
         msg = self.clients[id_frame].send(data_frame)
         if msg: self.on_data(id_frame, msg)
      else:
        tp, rsp = self.clients[id_frame].send(data_frame)   
        if rsp:
          self.sock.send(id_frame, zmq.SNDMORE |  zmq.NOBLOCK)
          self.sock.send(rsp, zmq.SNDMORE | zmq.NOBLOCK)
        if tp == "websocket":
           self.websockets.add(id_frame)
   @staticmethod
   def make_websocket_frame(msg):
       l = len(msg)
       if l < 126:
         return  chr(0x81) + chr(len(msg)) + msg
       else:
         return  struct.pack(">BBH", 0x81, 126, l) + msg

   def send(self, id_frame, data):
      self.sock.send(id_frame, zmq.SNDMORE |  zmq.NOBLOCK)
      self.sock.send(self.make_websocket_frame(rsp), zmq.SNDMORE | zmq.NOBLOCK)

   def on_data(self, id_frame, data):
       print repr(data)
   def loop(self):
    poller = zmq.Poller()
    poller.register(self.sock, flags=zmq.POLLIN)
    while 1:
      sockets =  dict(poller.poll())
      if sockets and sockets.get(self.sock) == zmq.POLLIN : 
        id_frame = self.sock.recv()
        data_frame = self.sock.recv()
        self.received(id_frame, data_frame) 

if __name__ == "__main__":
  ip = "127.0.0.1"
  port = 30001
  with open("index.html") as fp:
     index_page =[line for line in fp] 
   
  with open("index.html", "wb") as fp:
    for line in index_page:
      if "var ws_url" in line: line = "var ws_url=\"ws://{ip}:{port}\";\n".format(ip=ip, port=port); 
      fp.write(line) 
  server= Server(ip, port)
  server.loop()
