import os
import ast
import time
import json
import torch
import tokenize
import tempfile
import subprocess
from io import StringIO
from flask import Flask, request, jsonify
from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    AutoModelForSequenceClassification,
    AutoModelForTokenClassification,
    RobertaForTokenClassification
)

# ---------------- CONFIG ----------------

os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

GHOST_MODEL_PATH = "models/ghost_model"
FAST_MODEL_PATH  = "models/codebert-syntax-model"
DEEP_MODEL_PATH  = "models/deep_model"
SEM_MODEL_PATH = "models/error_locator_model" 

#LABELS = ["Syntax Issue","No Issue"]
"""LABELS = [
    "Syntax Issue",
    "Logical Issue",
    "Performance Issue",
    "Security Issue",
    "Maintainability Issue",
    "Style Issue",
    "No Issue"
]"""


app = Flask(__name__)

print("Ghost model path:", GHOST_MODEL_PATH)
#print("Fast model path :", FAST_MODEL_PATH)
print("Deep model path :", DEEP_MODEL_PATH)
print("Semantic model path :", SEM_MODEL_PATH)
# ---------------- LOAD MODELS ----------------

print("Loading Ghost model...")
ghost_tokenizer = AutoTokenizer.from_pretrained(
    GHOST_MODEL_PATH,
    local_files_only=True
)
ghost_model = AutoModelForCausalLM.from_pretrained(
    GHOST_MODEL_PATH,
    torch_dtype=torch.float16,
    device_map="auto",
    local_files_only=True
)
'''
print("Loading Fast Analysis model...")
fast_tokenizer = AutoTokenizer.from_pretrained(
    FAST_MODEL_PATH,
    local_files_only=True
)
fast_model = AutoModelForSequenceClassification.from_pretrained(
    FAST_MODEL_PATH,
    num_labels=2,
    local_files_only=True
)
'''
print("Loading Deep Analysis model...")
deep_tokenizer = AutoTokenizer.from_pretrained(
    DEEP_MODEL_PATH,
    local_files_only=True
)
deep_model = AutoModelForCausalLM.from_pretrained(
    DEEP_MODEL_PATH,
    torch_dtype=torch.float16,
    device_map="cpu",
    local_files_only=True
)

print("Loading Semantic Localization Model...")


sem_tokenizer = AutoTokenizer.from_pretrained(SEM_MODEL_PATH, use_fast=True)
sem_model = RobertaForTokenClassification.from_pretrained(SEM_MODEL_PATH)
sem_model.to(DEVICE)
sem_model.eval()


print("✅ All models loaded successfully!")

# ---------------- FUNCTIONS ----------------

def ghost_suggest(prefix,fast,loc):
    start = time.time()

    # ✅ Guard against empty input
    if prefix is None or prefix.strip() == "":
        return "", 0.0
    context =  prompt = f"""
# Python code completion
# Security: {fast}
# Semantic: {loc}
# prefix code : {prefix}
"""
    inputs = ghost_tokenizer(
        prefix,
        return_tensors="pt",
        truncation=True,
        padding=True
    ).to(ghost_model.device)

    # ✅ If tokenizer returned empty tokens
    if inputs["input_ids"].shape[1] == 0:
        inputs["input_ids"] = torch.tensor(
            [[ghost_tokenizer.eos_token_id]],
            device=ghost_model.device
        )

    outputs = ghost_model.generate(
        **inputs,
        max_new_tokens=20,
        do_sample=False,
        pad_token_id=ghost_tokenizer.eos_token_id
    )

    text = ghost_tokenizer.decode(outputs[0], skip_special_tokens=True)

    # safer extraction
    if text.startswith(prefix):
        suggestion = text[len(prefix):].strip()
    else:
        suggestion = text.strip()

    return suggestion, round(time.time() - start, 3)

"""
def fast_analyze(line):
    start = time.time()

    if line is None or line.strip() == "":
        return {
            "label": "No Issue",
            "confidence": 0.0,
            "latency": 0.0
        }

    inputs = fast_tokenizer(line, return_tensors="pt", truncation=True)

    with torch.no_grad():
        outputs = fast_model(**inputs)

    probs = torch.softmax(outputs.logits, dim=1)[0]
    conf, idx = torch.max(probs, dim=0)

    return {
        "label": LABELS[idx.item()],
        "confidence": round(conf.item(), 2),
        "latency": round(time.time() - start, 4)
    }
"""

def fast_analyze(code):
    start = time.time()

    if not code or not code.strip():
        return {
            "label": "Empty Input",
            "confidence": 0.0,
            "latency": 0.0,
            "issues": []
        }

    # Write code to temp file
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tmp:
        tmp.write(code.encode())
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ["bandit", "-f", "json", "-q", tmp_path],
            capture_output=True,
            text=True
        )

        try:
            bandit_output = json.loads(result.stdout)
        except json.JSONDecodeError:
            bandit_output = {}

        issues = []
        max_severity_score = 0

        severity_map = {
            "LOW": 0.3,
            "MEDIUM": 0.6,
            "HIGH": 0.9
        }

        for issue in bandit_output.get("results", []):
            severity = issue.get("issue_severity", "LOW")
            score = severity_map.get(severity, 0.3)

            max_severity_score = max(max_severity_score, score)

            issues.append({
                "line": issue.get("line_number"),
                "message": issue.get("issue_text"),
                "severity": severity
            })

        label = "Security Risk" if issues else "Clean"

        return {
            "label": label,
            "confidence": round(max_severity_score if issues else 0.95, 4),
            "latency": round(time.time() - start, 4),
            "issues": issues
        }

    finally:
        os.remove(tmp_path)


def deep_analyze(code,fast="",loc=""):
    if code is None or code.strip() == "":
        return {
            "explanation": "No code provided.",
            "latency": 0.0
        }
    try:
        ast.parse(code)
        ast_result = "No syntax errors"
    except Exception as e:
        ast_result = str(e)
    prompt = f"""
You are an expert Python debugging assistant.
Code:
{code}
Security analysis:
{fast}
Semantic suggestions (low confidence):
{loc}
AST result:
{ast_result}
Explain real issues and suggest improvements.
"""

    start = time.time()

    inputs = deep_tokenizer(prompt, return_tensors="pt").to(deep_model.device)

    outputs = deep_model.generate(
        **inputs,
        max_new_tokens=200,
        do_sample=False,
        pad_token_id=deep_tokenizer.eos_token_id
    )

    text = deep_tokenizer.decode(outputs[0], skip_special_tokens=True)

    return {
        "explanation": text,
        "latency": round(time.time() - start, 2)
    }

def chat_with_model(prompt, code):
    full_prompt = f"""

User question:
{prompt}

Code:
{code}

Answer clearly and helpfully:
"""

    inputs = deep_tokenizer(full_prompt, return_tensors="pt").to(deep_model.device)

    outputs = deep_model.generate(
        **inputs,
        max_new_tokens=250,
        do_sample=False,
        pad_token_id=deep_tokenizer.eos_token_id
    )

    text = deep_tokenizer.decode(outputs[0], skip_special_tokens=True)
    return text

# ---------------- TOKENIZER (MUST MATCH TRAINING) ----------------

def tokenize_code(code):
    tokens = []
    token_stream = tokenize.generate_tokens(StringIO(code).readline)

    for toknum, tokval, start, end, _ in token_stream:
        if toknum in [
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENDMARKER,
            tokenize.NL
        ]:
            continue

        if toknum == tokenize.STRING:
            tokens.append("<STR>")
        elif toknum == tokenize.NUMBER:
            tokens.append("<NUM>")
        elif tokval in ["True", "False"]:
            tokens.append("<BOOL>")
        else:
            tokens.append(tokval)

    return tokens

# ---------------- UTILITY ----------------

def merge_spans(spans):
    if not spans:
        return []

    spans = sorted(spans, key=lambda x: x["start"])
    merged = [spans[0]]

    for cur in spans[1:]:
        prev = merged[-1]
        if cur["start"] <= prev["end"]:
            prev["end"] = max(prev["end"], cur["end"])
            prev["confidence"] = max(prev["confidence"], cur["confidence"])
        else:
            merged.append(cur)

    return merged

# ---------------- PHASE 1: SYNTAX ----------------

'''def syntax_localize(code):
    try:
        ast.parse(code)
        return None
    except SyntaxError as e:
        if e.lineno and e.offset:
            lines = code.split("\n")
            char_offset = sum(len(lines[i]) + 1 for i in range(e.lineno - 1)) + (e.offset - 1)

            return {
                "start": char_offset,
                "end": char_offset + 1,
                "message": e.msg,
                "type": "syntax",
                "confidence": 1.0
            }

        return {
            "start": 0,
            "end": 1,
            "message": "Syntax Error",
            "type": "syntax",
            "confidence": 1.0
        }

'''
# ---------------- SYNTAX PHASE ----------------

def syntax_localize(code):
    try:
        ast.parse(code)
        return None
    except SyntaxError as e:
        if e.lineno and e.offset:
            lines = code.split("\n")
            char_offset = sum(len(lines[i]) + 1 for i in range(e.lineno - 1)) + (e.offset - 1)

            return {
                "start": char_offset,
                "end": char_offset + 1,
                "message": e.msg,
                "type": "syntax"
            }

        return {
            "start": 0,
            "end": 1,
            "message": "Syntax Error",
            "type": "syntax"
        }

# ---------------- PHASE 2: SEMANTIC ----------------
'''
def semantic_localize(code, threshold=0.85):

    inputs = sem_tokenizer(
        code,
        return_tensors="pt",
        truncation=True,
        return_offsets_mapping=True
    ).to(DEVICE)

    offsets = inputs.pop("offset_mapping")

    with torch.no_grad():
        outputs = sem_model(**inputs)

    probs = torch.softmax(outputs.logits, dim=2)[0]

    spans = []

    for i, token_probs in enumerate(probs):
        conf, label = torch.max(token_probs, dim=0)

        if label.item() == 1 and conf.item() > threshold:
            start, end = offsets[0][i].tolist()

            if start != end:
                spans.append({
                    "start": start,
                    "end": end,
                    "confidence": round(conf.item(), 3),
                    "type": "semantic"
                })

    return merge_spans(spans)'''

def semantic_localize(code, threshold=0.85):

    tokens = tokenize_code(code)

    encoding = sem_tokenizer(
        tokens,
        is_split_into_words=True,
        return_tensors="pt",
        truncation=True,
        max_length=512
    )

    word_ids = encoding.word_ids()

    input_ids = encoding["input_ids"].to(DEVICE)
    attention_mask = encoding["attention_mask"].to(DEVICE)

    with torch.no_grad():
        outputs = sem_model(input_ids=input_ids, attention_mask=attention_mask)

    probs = torch.softmax(outputs.logits, dim=-1)[0]
    preds = torch.argmax(probs, dim=-1)

    spans = []
    current_word = None

    # We must convert word index back to character span
    token_stream = list(tokenize.generate_tokens(StringIO(code).readline))
    word_positions = []

    for toknum, tokval, start, end, _ in token_stream:
        if toknum in [
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENDMARKER,
            tokenize.NL
        ]:
            continue

        char_start = sum(len(line) + 1 for line in code.split("\n")[:start[0]-1]) + start[1]
        char_end = sum(len(line) + 1 for line in code.split("\n")[:end[0]-1]) + end[1]

        word_positions.append((char_start, char_end))

    for i, word_id in enumerate(word_ids):
        if word_id is None:
            continue

        conf = probs[i][1].item()
        label = preds[i].item()

        if label == 1 and conf > threshold:
            if word_id < len(word_positions):
                start, end = word_positions[word_id]
                spans.append({
                    "start": start,
                    "end": end,
                    "confidence": round(conf, 3),
                    "type": "semantic"
                })

    return spans


# ---------------- ROUTES ----------------

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "AI Code Analyzer running"})


@app.route("/ghost", methods=["POST"])
def ghost_api():
    data = request.json or {}
    print(data)
    prefix = data.get("code", "")
    fast = data.get("fastdata","")
    loc = data.get("location","")
    suggestion, latency = ghost_suggest(prefix , fast , loc)
    print(suggestion)
    return jsonify({
        "suggestion": suggestion,
        "latency": latency
    })


@app.route("/fast", methods=["POST"])
def fast_api():
    data = request.json or {}
    code = data.get("code", "")
    
    result = fast_analyze(code)
    print(result)

    return jsonify(result)


@app.route("/deep", methods=["POST"])
def deep_api():
    data = request.json or {}
    code = data.get("code", "")
    fast = data.get("fastanalysis","")
    loc = data.get("semantic")
    result = deep_analyze(code,fast,loc)
    print(result)
    return jsonify({
        "issues": [
            {
                "message": "Deep Analysis Result",
                "explanation": result["explanation"],
                "latency": result["latency"]
            }
        ]
    })

@app.route("/chat", methods=["POST"])
def chat_api():
    data = request.json or {}
    prompt = data.get("prompt", "")
    code = data.get("code", "")

    reply = chat_with_model(prompt, code)

    return jsonify({
        "reply": reply
    })

@app.route("/localize", methods=["POST"])
def localize_api():
    data = request.json or {}
    fullcode = data.get("fullcode","")
    code = data.get("code", "")
    base_offset = data.get("base_offset", 0)
    print("localize route activated")
    print(f" fullcode : {fullcode} \n window: {code}")
    # Phase 1 → Syntax
    syntax_error = syntax_localize(fullcode)

    if syntax_error:
        print("syntax_error")
        print(syntax_error)
        return jsonify({
            "phase": "syntax",
            "errors": [syntax_error]
        })

    # Phase 2 → Semantic
    print("semantic case")
    semantic_errors = semantic_localize(code)
    print(semantic_errors)
    for span in semantic_errors:
        span["start"] += base_offset
        span["end"] += base_offset
    return jsonify({
        "phase": "semantic",
        "errors": semantic_errors
    })

# ---------------- RUN SERVER ----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
