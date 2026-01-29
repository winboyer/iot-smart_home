import requests
import json
import openai

def query_vllm_service(prompt: str, 
                       model: str = "/home/jinyfeng/models/Baichuan/Baichuan-M2-32B", 
                       max_tokens: int = 1024):
    """
    Query a vLLM deployed service
    
    Args:
        prompt: Input prompt text
        model: Model name
        max_tokens: Maximum tokens to generate
    
    Returns:
        Generated text response
    """

    url = "http://127.0.0.1:2602/v1"
    # url = "http://127.0.0.1:2602/v1/completions"

    client = openai.OpenAI(base_url=url, api_key="none")
    stream = client.chat.completions.create(
        model = model,
        messages = [{"role": "user", "content": prompt}],
        stream = True,

    )    
    return stream
    # for chunk in stream:
    #     if chunk.choices[0].delta.content:
    #         # print(chunk.choices[0].delta.content)
    #         # print(chunk.choices[0].delta.content, end="", flush=True)
    #         # response_message = (chunk.choices[0].delta.content, end="", flush=True)
    #         return chunk.choices[0].delta.content
  
    # payload = {
    #     "model": model,
    #     "prompt": prompt,
    #     "max_tokens": max_tokens,
    #     "temperature": 0.7,
    #     "top_p": 0.9
    # }
    
    # try:
    #     response = requests.post(url, json=payload, headers=headers)
    #     response.raise_for_status()
    #     result = response.json()
    #     print(result["choices"][0]["text"])
    #     return result["choices"][0]["text"]
    # except requests.exceptions.RequestException as e:
    #     print(f"Error: {e}")
    #     return None


# Test example
if __name__ == "__main__":
    prompt = "你好, 你能诊断哪些疾病?"
    response = query_vllm_service(prompt)
    # print(response)

    for chunk in response:
        if chunk.choices[0].delta.content:
            # print(chunk.choices[0].delta.content)
            print(chunk.choices[0].delta.content, end="", flush=True)
            # response_message = (chunk.choices[0].delta.content, end="", flush=True)
            
    