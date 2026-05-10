import stomp
import logging

logging.basicConfig(level=logging.DEBUG)
logging.verbose = True

conn = stomp.Connection([('ems1.swim.faa.gov', 55443)], reconnect_attempts_max=0)
conn.set_ssl(for_hosts=[('ems1.swim.faa.gov', 55443)])
try:
    conn.connect(wait=True)
except Exception as e:
    print("Caught:", type(e).__name__)