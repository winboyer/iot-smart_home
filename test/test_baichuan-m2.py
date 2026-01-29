# 1. load model
from transformers import AutoTokenizer, AutoModelForCausalLM
# from modelscope import AutoTokenizer, AutoModelForCausalLM
import os
os.environ['CUDA_VISIBLE_DEVICES'] = '0, 1'

model_path = "/home/jinyfeng/models/Baichuan/Baichuan-M2-32B"
model = AutoModelForCausalLM.from_pretrained(model_path, trust_remote_code=True)
tokenizer = AutoTokenizer.from_pretrained(model_path, device_map="auto")
# 2. Input prompt text
prompt = "Got a big swelling after a bug bite. Need help reducing it."
# 3. Encode the input text for the model
messages = [
    {"role": "user", "content": prompt}
]
text = tokenizer.apply_chat_template(
    messages,
    tokenize=False,
    add_generation_prompt=True,
    thinking_mode='on' # on/off/auto 
)
model_inputs = tokenizer([text], return_tensors="pt").to(model.device)
# 4. Generate text
generated_ids = model.generate(
    **model_inputs,
    max_new_tokens=4096
)
output_ids = [
    output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
][0].tolist()
# 5. parsing thinking content
try:
    # rindex finding 151668 (</think>)
    index = len(output_ids) - output_ids[::-1].index(151668)
except ValueError:
    index = 0

thinking_content = tokenizer.decode(output_ids[:index], skip_special_tokens=True).strip("")
content = tokenizer.decode(output_ids[index:], skip_special_tokens=True).strip("")

print("thinking content:", thinking_content)
print("content:", content)
