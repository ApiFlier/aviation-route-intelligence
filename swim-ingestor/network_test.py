import socket
import ssl
import sys

def test_reachability(host, port):
    print(f"Testing reachability to {host}:{port}")
    # DNS Test
    try:
        ip = socket.gethostbyname(host)
        print(f"DNS_OK: {host} resolved to {ip}")
    except Exception as e:
        print(f"DNS_FAIL: {type(e).__name__} {str(e)[:200]}")
        sys.exit(1)
        
    # TCP Test
    try:
        with socket.create_connection((host, port), timeout=15) as sock:
            print(f"TCP_OK: Connected to {host}:{port}")
            
            # TLS Test
            try:
                context = ssl.create_default_context()
                with context.wrap_socket(sock, server_hostname=host) as ssock:
                    print(f"TLS_OK: Handshake successful with {host}")
                    print(f"TLS Cipher: {ssock.cipher()}")
            except Exception as e:
                print(f"TLS_FAIL: {type(e).__name__} {str(e)[:200]}")
    except Exception as e:
        print(f"TCP_FAIL: {type(e).__name__} {str(e)[:200]}")
        sys.exit(1)

if __name__ == "__main__":
    host = "ems1.swim.faa.gov"
    port = 55443
    test_reachability(host, port)