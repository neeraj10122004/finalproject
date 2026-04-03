const vscode = require("vscode");
const fetch = require("node-fetch");

class DeepAnalysisViewProvider {
  constructor(context) {
    this.context = context;
    this.view = null;
  }

  resolveWebviewView(webviewView) {
    this.view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [
      vscode.Uri.joinPath(this.context.extensionUri, "media")
    ]
    };

    const markedUri = webviewView.webview.asWebviewUri(
    vscode.Uri.joinPath(this.context.extensionUri, "media", "marked.min.js")
  );

    webviewView.webview.html = this.getHtml(markedUri);

    
    webviewView.webview.onDidReceiveMessage(async (msg) => {
      if (msg.type === "userPrompt") {
        await this.handleUserChat(msg.text);
      }
    });
  }

  async handleUserChat(userText) {
    const editor = vscode.window.activeTextEditor;
    const code = editor ? editor.document.getText() : "";

   
    this.view.webview.postMessage({
      type: "userMessage",
      text: userText
    });

    try {
      const response = await fetch("http://localhost:5000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: userText,
          code: code , 
        })
      });

      const data = await response.json();

      this.view.webview.postMessage({
        type: "botReply",
        text: data.reply || "No response from backend."
      });

    } catch (err) {
      console.error("Chat backend error:", err);
      this.view.webview.postMessage({
        type: "botReply",
        text: " Backend error."
      });
    }
  }

  
  appendSystemMessage(text) {
    if (!this.view) return;

    this.view.webview.postMessage({
      type: "botReply",
      text: " Auto Analysis (on save):\n\n" + text
    });
  }

  getHtml(markedUri) {
    return `
<!DOCTYPE html>
<html>
<head>
<style>
body {
  font-family: Arial, sans-serif;
  padding: 10px;
  background-color: #1e1e1e;
  color: white;
}
#chat {
  height: 70vh;
  overflow-y: auto;
  border: 1px solid #444;
  padding: 10px;
}
.user {
  background: #007acc;
  padding: 8px;
  margin: 6px;
  border-radius: 8px;
  text-align: right;
}
.bot {
  background: #333;
  padding: 8px;
  margin: 6px;
  border-radius: 8px;
  text-align: left;
}
button {
  background: #0e639c;
  border: none;
  color: white;
  padding: 6px 10px;
  border-radius: 4px;
  cursor: pointer;
  margin-right: 4px;
}
input {
  width: 78%;
  padding: 6px;
  border-radius: 4px;
  border: none;
}
</style>
</head>
<body>

<div>
  <button onclick="sendPreset('Explain this code')">Explain</button>
  <button onclick="sendPreset('Find bugs in this code')">Find Bugs</button>
  <button onclick="sendPreset('Optimize this code')">Optimize</button>
</div>

<div id="chat"></div>

<input id="input" placeholder="Ask about your code..." />
<button onclick="send()">Send</button>

<script src="${markedUri}"></script>
<script>
const vscode = acquireVsCodeApi();
const chat = document.getElementById("chat");
const input = document.getElementById("input");

function send(){
  const text = input.value;
  if(!text) return;
  vscode.postMessage({ type:"userPrompt", text });
  input.value="";
}

function sendPreset(text){
  vscode.postMessage({ type:"userPrompt", text });
}

window.addEventListener("message", event => {
  const msg = event.data;

  if(msg.type === "userMessage"){
    chat.innerHTML += '<div class="user">'+msg.text+'</div>';
  }

  if(msg.type === "botReply"){
    const html = marked.parse(msg.text);
    chat.innerHTML += '<div class="bot">'+html+'</div>';
    chat.scrollTop = chat.scrollHeight;
  }
});
</script>

</body>
</html>
`;
  }
}

module.exports = DeepAnalysisViewProvider;
