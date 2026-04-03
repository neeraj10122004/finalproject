const vscode = require("vscode");
const fetch = require("node-fetch");
const DeepAnalysisViewProvider = require("./deepView");

const fastOutput = vscode.window.createOutputChannel("Fast Analysis");

let fastAnalysisDebounceTimer = null;
let debounceTimer = null;
const DEBOUNCE_DELAY = 1000;
const DEBOUNCE_DELAY2 = 1200;
let deepViewProvider;
let diagnosticCollection;
let lastFastResult = null;
let lastErrorLocations = null;

function activate(context) {
  console.log("✅ Extension Activated");

  const provider = vscode.languages.registerInlineCompletionItemProvider(
    { scheme: "file", language: "python" },
    {
      async provideInlineCompletionItems(document, position) {
        return new Promise((resolve) => {
          if (debounceTimer) clearTimeout(debounceTimer);

          debounceTimer = setTimeout(async () => {
            const items = await generateSuggestion(document, position, lastFastResult , lastErrorLocations);
            resolve(items);
          }, DEBOUNCE_DELAY2);
        });
      }
    }
  );

  deepViewProvider = new DeepAnalysisViewProvider(context);

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(
      "deepAnalysisView",
      deepViewProvider
    ),
    provider,
    vscode.commands.registerCommand("mlExtension.runDeepFromFast", () => {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return;

    sendForAnalysis(editor.document, true,null,null,`fast result: ${lastFastResult} localized errors : ${lastErrorLocations}`); // true = deep
  })
  );
  diagnosticCollection = vscode.languages.createDiagnosticCollection("mlErrors");
  context.subscriptions.push(diagnosticCollection);

 
  vscode.workspace.onDidChangeTextDocument(event => {
    const doc = event.document;
    if (!isSupported(doc)) return;

    if (fastAnalysisDebounceTimer) clearTimeout(fastAnalysisDebounceTimer);

    diagnosticCollection.delete(event.document.uri); 
    
    const change = event.contentChanges[0];
    const startOffset = doc.offsetAt(change.range.start);
    const endOffset = doc.offsetAt(change.range.end); 

    fastAnalysisDebounceTimer = setTimeout(() => {
      sendForAnalysis(doc, false, startOffset, endOffset, null);
    }, DEBOUNCE_DELAY);
  });

  
  vscode.workspace.onDidSaveTextDocument(doc => {
    if (!isSupported(doc)) return;
    sendForAnalysis(doc, true ,null, null, null);
  });
}

function isSupported(doc) {
  return ["python"].includes(doc.languageId);
}

async function sendForAnalysis(doc, deep, startOffset=null, endOffset=null, context=null) {
  const code = doc.getText();

  try {
    let response;
    const url = deep
      ? "http://localhost:5000/deep"
      : "http://localhost:5000/fast";
    if(lastFastResult!=null && lastErrorLocations!=null){
      response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code , fastanalysis: lastFastResult , semantic: lastErrorLocations})
      });
      context=null;
    }
    else{
      response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code })
      });
    }
    
    
    const result = await response.json();

    if (!deep) {
    
      fastOutput.appendLine(" FAST SECURITY ANALYSIS");
      fastOutput.appendLine("----------------------------------");

      fastOutput.appendLine(`Label      : ${result.label}`);
      fastOutput.appendLine(`Confidence : ${result.confidence}`);
      fastOutput.appendLine(`Latency    : ${result.latency}s`);

      if (result.issues && result.issues.length > 0) {
          fastOutput.appendLine("\n Issues Found:");

          result.issues.forEach((issue, index) => {
              fastOutput.appendLine(
                  `${index + 1}. [Line ${issue.line}] (${issue.severity}) ${issue.message}`
              );
          });
      } else {
          fastOutput.appendLine("\n No security issues detected.");
      }

      fastOutput.appendLine("----------------------------------\n");
      
      lastFastResult=JSON.stringify(result);
      fastOutput.appendLine("");
      fastOutput.appendLine("[Run Deep Analysis](command:mlExtension.runDeepFromFast)");


      await localizeErrors(doc, startOffset, endOffset);
      
      vscode.window.showInformationMessage(
        `Fast Result: ${result.label}`,
        "Run Deep Analysis"
      ).then(selection => {
      if (selection === "Run Deep Analysis") {
        vscode.commands.executeCommand("mlExtension.runDeepFromFast");
      }
      });
     
      

    } else {
      let chatMessage = "";

      result.issues.forEach(issue => {
        chatMessage += `• ${issue.message}\n`;
        if (issue.explanation) {
          chatMessage += `  ${issue.explanation}\n`;
        }
      });

      if (deepViewProvider?.view) {
        deepViewProvider.appendSystemMessage(chatMessage);
      }
    }

  } catch (err) {
    if(deep){
    console.error("Backend error", err);
    if (deepViewProvider?.view) {
      deepViewProvider.appendSystemMessage(" Backend Error.");
    }
  }
  else{
    fastOutput.appendLine(" FAST ANALYSIS");
    fastOutput.appendLine(`- BackendError`);
  }
  }
}



async function localizeErrors(doc, changeStart, changeEnd) {

  try {
    const fullText = doc.getText();

    const WINDOW_CHARS = 2000; 

    const windowStart = Math.max(0, changeStart - WINDOW_CHARS);
    const windowEnd = Math.min(fullText.length, changeEnd + WINDOW_CHARS);
    const windowCode = fullText.substring(windowStart, windowEnd);
    const response = await fetch("http://localhost:5000/localize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fullcode: fullText,
        code: windowCode,
        base_offset: windowStart
      })
    });

    const result = await response.json();
    lastErrorLocations = JSON.stringify(result);

    showDiagnostics(doc, result.errors || [],result.phase);

  } catch (err) {
    console.error("Localization backend error", err);
  }
}

function showDiagnostics(document, errors, phase) {

  if (!diagnosticCollection) return;

  const diagnostics = [];

  errors.forEach(error => {

    const startPos = document.positionAt(error.start);
    const endPos = document.positionAt(error.end);

    const range = new vscode.Range(startPos, endPos);
    let diagnostic;
    const severity =
      phase === "syntax"
        ? vscode.DiagnosticSeverity.Error
        : vscode.DiagnosticSeverity.Warning; 
    if(phase ==="syntax"){
      diagnostic = new vscode.Diagnostic(
        range,
        `Static Check : ${error.message}`,
        severity
      );
    }
    else{
      diagnostic = new vscode.Diagnostic(
        range,
        `Semantic ML Model Detected Error : ${error.confidence}`,
        severity
      );
    }
    

    diagnostics.push(diagnostic);
  });

  diagnosticCollection.set(document.uri, diagnostics);
}


async function generateSuggestion(document, position , fast=null , location=null ) {
  const linePrefix = document
    .lineAt(position)
    .text.substring(0, position.character);



  const textBeforeCursor = document.getText(
    new vscode.Range(new vscode.Position(0, 0), position)
  );

  try {
    let response;
    if(fast!=null && location!=null){
    response = await fetch("http://localhost:5000/ghost", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: textBeforeCursor , fastdata: fast, location: location })
    });
    }
    else{
      response = await fetch("http://localhost:5000/ghost", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: textBeforeCursor })
    });
    }


    const data = await response.json();

    return [{
      insertText: data.suggestion || "BackendError",
      range: new vscode.Range(position, position)
    }];

  } catch (err) {
    return [{
      insertText: "BackendError",
      range: new vscode.Range(position, position)
    }];
  }
}

function deactivate() {}

module.exports = {
  activate,
  deactivate
};
