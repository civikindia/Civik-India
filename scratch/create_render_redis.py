import urllib.request
import json
import ssl

API_KEY = "rnd_fXA530LmlrcGiZrzmuCcRiRpH5uX"

def make_request(url, method="POST", body=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode("utf-8")
    else:
        data = None
    
    context = ssl.create_default_context()
    
    try:
        with urllib.request.urlopen(req, data=data, context=context) as response:
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
    body = {
        "name": "civikindia-redis",
        "ownerId": "tea-d89dhd99rddc7396mg3g",
        "plan": "free",
        "region": "singapore"
    }
    print("=== CREATING KEY VALUE INSTANCE ===")
    status, response = make_request("https://api.render.com/v1/key-value", body=body)
    print(f"Status code: {status}")
    print("Response:", json.dumps(response, indent=2))
    
    # Save the output
    with open("scratch/create_redis_response.json", "w") as f:
        json.dump({"status": status, "response": response}, f, indent=2)

if __name__ == "__main__":
    main()
