import os
import stomp
import logging

logging.basicConfig(level=logging.DEBUG)

def test():
    host = "ems1.swim.faa.gov"
    port = 55443
    faa_user = os.environ.get("FAA_USER")
    faa_pass = os.environ.get("FAA_PASS")
    
    class Listener(stomp.ConnectionListener):
        def on_error(self, frame):
            print("ERROR", frame.headers, frame.body)
        def on_connected(self, frame):
            print("CONNECTED")
            
    conn = stomp.Connection([(host, port)], reconnect_attempts_max=0)
    conn.set_ssl(for_hosts=[(host, port)])
    conn.set_listener('', Listener())
    print("Connecting...")
    try:
        conn.connect(login=faa_user, passcode=faa_pass, wait=True)
        print("Success")
    except Exception as e:
        print("Failed:", type(e).__name__, str(e))
        
if __name__ == "__main__":
    test()