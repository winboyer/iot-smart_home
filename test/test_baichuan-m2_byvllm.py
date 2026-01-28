import requests
import json

def query_vllm_service(prompt: str, model: str = "baichuan-7b", max_tokens: int = 512):
    """
    Query a vLLM deployed service
    
    Args:
        prompt: Input prompt text
        model: Model name
        max_tokens: Maximum tokens to generate
    
    Returns:
        Generated text response
    """
    url = "http://localhost:8000/v1/completions"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "top_p": 0.9
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["text"]
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")
        return None


# Test example
if __name__ == "__main__":
    prompt = "Hello, what is IoT smart home?"
    response = query_vllm_service(prompt)
    print(response)