import urllib.request
import json
import ssl

API_KEY = "rnd_fXA530LmlrcGiZrzmuCcRiRpH5uX"
OWNER_ID = "tea-d89dhd99rddc7396mg3g"
SERVICE_ID = "srv-d8a4tdbeo5us739e774g"

def make_request(url, method="GET", body=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Accept", "application/json")
    
    context = ssl.create_default_context()
    
    try:
        with urllib.request.urlopen(req, data=body, context=context) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
            return e.code, json.loads(err_body)
        except Exception:
            return e.code, str(e)
    except Exception as e:
        return 0, str(e)

def main():
    url = f"https://api.render.com/v1/logs?ownerId={OWNER_ID}&resource={SERVICE_ID}&limit=1000"
    status, response = make_request(url)
    print(f"Status: {status}")
    if status == 200:
        logs = response.get("logs", [])
        print(f"Retrieved {len(logs)} log entries.")
        with open("scratch/deploy_logs.txt", "w", encoding="utf-8") as f:
            for log in logs:
                ts = log.get("timestamp")
                msg = log.get("message")
                f.write(f"[{ts}] {msg}\n")
        print("Wrote logs to scratch/deploy_logs.txt")
    else:
        print(f"Error: {response}")

if __name__ == "__main__":
    main()
