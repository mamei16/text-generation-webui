// -------------------------------------------------
// Event handlers
// -------------------------------------------------

function copyToClipboard(element) {
  if (!element) return;

  const messageElement = element.closest(".message, .user-message, .assistant-message");
  if (!messageElement) return;

  const rawText = messageElement.getAttribute("data-raw");
  if (!rawText) return;

  navigator.clipboard.writeText(rawText).then(function() {
    const originalSvg = element.innerHTML;
    element.innerHTML = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\" class=\"text-green-500 dark:text-green-400\"><path d=\"M5 12l5 5l10 -10\"></path></svg>";
    setTimeout(() => {
      element.innerHTML = originalSvg;
    }, 1000);
  }).catch(function(err) {
    console.error("Failed to copy text: ", err);
  });
}

function branchHere(element) {
  if (!element) return;

  const messageElement = element.closest(".message, .user-message, .assistant-message");
  if (!messageElement) return;

  const index = messageElement.getAttribute("data-index");
  if (!index) return;

  const branchIndexInput = document.getElementById("Branch-index").querySelector("input");
  if (!branchIndexInput) {
    console.error("Element with ID 'Branch-index' not found.");
    return;
  }
  const branchButton = document.getElementById("Branch");

  if (!branchButton) {
    console.error("Required element 'Branch' not found.");
    return;
  }

  branchIndexInput.value = index;

  // Trigger any 'change' or 'input' events Gradio might be listening for
  const event = new Event("input", { bubbles: true });
  branchIndexInput.dispatchEvent(event);

  branchButton.click();
}

// -------------------------------------------------
// Message Editing Functions
// -------------------------------------------------

function editHere(buttonElement) {
  if (!buttonElement) return;

  const messageElement = buttonElement.closest(".message, .user-message, .assistant-message");
  if (!messageElement) return;

  const messageBody = messageElement.querySelector(".message-body");
  if (!messageBody) return;

  // If already editing, focus the textarea
  const existingTextarea = messageBody.querySelector(".editing-textarea");
  if (existingTextarea) {
    existingTextarea.focus();
    return;
  }

  // Determine role based on message element - handle different chat modes
  const isUserMessage = messageElement.classList.contains("user-message") ||
                       messageElement.querySelector(".text-you") !== null ||
                       messageElement.querySelector(".circle-you") !== null;

  startEditing(messageElement, messageBody, isUserMessage);
}

function startEditing(messageElement, messageBody, isUserMessage) {
  const rawText = messageElement.getAttribute("data-raw") || messageBody.textContent;
  const originalHTML = messageBody.innerHTML;

  // Create editing interface
  const editingInterface = createEditingInterface(rawText);

  // Replace message content
  messageBody.innerHTML = "";
  messageBody.appendChild(editingInterface.textarea);
  messageBody.appendChild(editingInterface.controls);

  editingInterface.textarea.focus();
  editingInterface.textarea.setSelectionRange(rawText.length, rawText.length);

  // Setup event handlers
  setupEditingHandlers(editingInterface.textarea, messageElement, originalHTML, messageBody, isUserMessage);
}

function createEditingInterface(text) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.className = "editing-textarea";
  textarea.rows = Math.max(3, text.split("\n").length);

  const controls = document.createElement("div");
  controls.className = "edit-controls-container";

  const saveButton = document.createElement("button");
  saveButton.textContent = "Save";
  saveButton.className = "edit-control-button";
  saveButton.type = "button";

  const cancelButton = document.createElement("button");
  cancelButton.textContent = "Cancel";
  cancelButton.className = "edit-control-button edit-cancel-button";
  cancelButton.type = "button";

  controls.appendChild(saveButton);
  controls.appendChild(cancelButton);

  return { textarea, controls, saveButton, cancelButton };
}

function setupEditingHandlers(textarea, messageElement, originalHTML, messageBody, isUserMessage) {
  const saveButton = messageBody.querySelector(".edit-control-button:not(.edit-cancel-button)");
  const cancelButton = messageBody.querySelector(".edit-cancel-button");

  const submitEdit = () => {
    const index = messageElement.getAttribute("data-index");
    if (!index || !submitMessageEdit(index, textarea.value, isUserMessage)) {
      cancelEdit();
    }
  };

  const cancelEdit = () => {
    messageBody.innerHTML = originalHTML;
  };

  // Event handlers
  saveButton.onclick = submitEdit;
  cancelButton.onclick = cancelEdit;

  textarea.onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submitEdit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancelEdit();
    }
  };
}

function submitMessageEdit(index, newText, isUserMessage) {
  const editIndexInput = document.getElementById("Edit-message-index")?.querySelector("input");
  const editTextInput = document.getElementById("Edit-message-text")?.querySelector("textarea");
  const editRoleInput = document.getElementById("Edit-message-role")?.querySelector("textarea");
  const editButton = document.getElementById("Edit-message");

  if (!editIndexInput || !editTextInput || !editRoleInput || !editButton) {
    console.error("Edit elements not found");
    return false;
  }

  editIndexInput.value = index;
  editTextInput.value = newText;
  editRoleInput.value = isUserMessage ? "user" : "assistant";

  editIndexInput.dispatchEvent(new Event("input", { bubbles: true }));
  editTextInput.dispatchEvent(new Event("input", { bubbles: true }));
  editRoleInput.dispatchEvent(new Event("input", { bubbles: true }));

  editButton.click();
  return true;
}

function navigateVersion(element, direction) {
  if (!element) return;

  const messageElement = element.closest(".message, .user-message, .assistant-message");
  if (!messageElement) return;

  const index = messageElement.getAttribute("data-index");
  if (!index) return;

  // Determine role based on message element classes
  let role = "assistant"; // Default role
  if (messageElement.classList.contains("user-message") ||
      messageElement.querySelector(".text-you") ||
      messageElement.querySelector(".circle-you")) {
    role = "user";
  }

  const indexInput = document.getElementById("Navigate-message-index")?.querySelector("input");
  const directionInput = document.getElementById("Navigate-direction")?.querySelector("textarea");
  const roleInput = document.getElementById("Navigate-message-role")?.querySelector("textarea");
  const navigateButton = document.getElementById("Navigate-version");

  if (!indexInput || !directionInput || !roleInput || !navigateButton) {
    console.error("Navigation control elements (index, direction, role, or button) not found.");
    return;
  }

  indexInput.value = index;
  directionInput.value = direction;
  roleInput.value = role;

  // Trigger 'input' events for Gradio to pick up changes
  const event = new Event("input", { bubbles: true });
  indexInput.dispatchEvent(event);
  directionInput.dispatchEvent(event);
  roleInput.dispatchEvent(event);

  navigateButton.click();
}

function regenerateClick() {
  document.getElementById("Regenerate").click();
}

function continueClick() {
  document.getElementById("Continue").click();
}

function removeLastClick() {
  document.getElementById("Remove-last").click();
}
//////////////////////////////////////////////////////
//
// Context progress bar
//
//////////////////////////////////////////////////////
function darkModeEnabled() {
    var currentCSS = document.getElementById("highlight-css");
    return currentCSS.getAttribute("href") === "file/css/highlightjs/github-dark.min.css";
}

function toggleProgressBarDarkMode() {
  var progressContainer = document.querySelector('.progress-container');
  if (darkModeEnabled()) {
    var lightGray = getComputedStyle(document.body).getPropertyValue("--light-gray");
    progressContainer.style.backgroundColor = lightGray;
  } else {
    progressContainer.style.backgroundColor = "#f3f4f6";
  }
  updateProgressBar(progressContainer.lastPercentage);
}


function getColor(percentage) {
    if (percentage == 0) return "rgba(0, 0, 0, 0)";

    const bright_colors = [
        { stop: 0, color: [0, 123, 255] },
        { stop: 25, color: [0, 255, 0] },
        { stop: 50, color: [255, 255, 0] },
        { stop: 75, color: [255, 165, 0] },
        { stop: 100, color: [255, 0, 0] }
    ];

    const muted_colors = [
        { stop: 0, color: [0, 61, 128] },       // Muted blue
        { stop: 25, color: [0, 128, 0] },       // Muted green
        { stop: 50, color: [128, 128, 0] },     // Muted yellow
        { stop: 75, color: [128, 82, 0] },      // Muted orange
        { stop: 100, color: [128, 0, 0] }       // Muted red
    ];

    const colors = darkModeEnabled()? muted_colors:bright_colors;

    let startColor, endColor, startStop, endStop;
    for (let i = 0; i < colors.length - 1; i++) {
        if (percentage >= colors[i].stop && percentage <= colors[i + 1].stop) {
            startColor = colors[i].color;
            endColor = colors[i + 1].color;
            startStop = colors[i].stop;
            endStop = colors[i + 1].stop;
            break;
        }
    }

    const ratio = (percentage - startStop) / (endStop - startStop);
    const r = Math.round(startColor[0] + ratio * (endColor[0] - startColor[0]));
    const g = Math.round(startColor[1] + ratio * (endColor[1] - startColor[1]));
    const b = Math.round(startColor[2] + ratio * (endColor[2] - startColor[2]));

    return `rgb(${r}, ${g}, ${b})`;
}


function updateProgressBar(percentage) {
    if (percentage === undefined) percentage = 0;
    const progressBar = document.querySelector('.progress-bar');
    progressBar.style.width = percentage + '%';
    progressBar.style.backgroundColor = getColor(percentage);
}

let lastPercentage;
////////////////////////////////////////////////////////////

function throttle(fn) {
    let rafId = null;
    let pendingData = null;

    return function (...args) {
        const data = JSON.parse(args[0].data);

        if (data.forceRender) {
            if (rafId) cancelAnimationFrame(rafId);
            fn.call(this, data);
            rafId = null; // Clear since we executed
            pendingData = null;
        } else {
            lastPercentage = data.contextFillPercentage ? data.contextFillPercentage : lastPercentage;
            updateProgressBar(lastPercentage);
            pendingData = data;
            if (!rafId) {
                rafId = requestAnimationFrame(() => {
                    fn.call(this, pendingData);
                    rafId = null;
                    pendingData = null;
                });
            }
        }
    };
}

var ws_protocol = window.location.protocol == "https:" ? "wss" : "ws";

if (!window.gradio_config.auth_required) {
    const ws = new WebSocket(ws_protocol + "://" + window.location.host + "/ws");

    ws.onmessage = throttle((data) => {
        handleMorphdomUpdate(data);
    });
}
// True when forceRender is false
let currentlyGenerating;
let skipUIUpdatesDuringStreaming = true;

// True as long as model is processing or generating
let isCurrentlyGenerating = false;

let targetElement;

// Create a MutationObserver instance
const mutationObserver = new MutationObserver(function() {
    if (targetElement.classList.contains("_generating")) {
        typing.parentNode.classList.add("visible-dots");
        document.getElementById("stop").style.display = "flex";
        document.getElementById("Generate").style.display = "none";
        if (!isCurrentlyGenerating) {
            isCurrentlyGenerating = true;
            // Scroll to bottom after submitting a new message
            setTimeout(() => {
                targetElement.querySelector("#scrollAnchor").scrollIntoView()
            }, 200);
        }
    } else {
        typing.parentNode.classList.remove("visible-dots");
        document.getElementById("stop").style.display = "none";
        document.getElementById("Generate").style.display = "flex";
        isCurrentlyGenerating = false;
    }
});

// Configure the observer to watch for changes in the subtree and attributes
const config = {
    childList: true,
    subtree: true,
    characterData: true,
    attributeFilter: ['class']
};


function isElementVisibleOnScreen(element) {
    if (element.isVisibleOnScreen === undefined) {
        const rect = element.getBoundingClientRect();
        return (
            rect.left < window.innerWidth &&
            rect.right > 0 &&
            rect.top < window.innerHeight &&
            rect.bottom > 0
        );
    } else {
        return element.isVisibleOnScreen;
    }
}

const intersectObserver = new IntersectionObserver((entries) => {
    mutationObserver.disconnect();
    for (const entry of entries) {
        entry.target.isVisibleOnScreen = entry.isIntersecting;
        if (!currentlyGenerating && entry.isIntersecting) {
            if (entry.target.className === "message-body")
                doSyntaxHighlighting(entry.target);
            else if (entry.target.matches("pre code:not([data-highlighted])"))
                syntaxHighlightCodeBlock(entry.target);
            else if (entry.target.matches("p, span, li, td, th, h1, h2, h3, h4, h5, h6, blockquote, figcaption,"
                                       + "caption, dd, dt"))
                syntaxHighlightKatex(entry.target);
        }
    }
    mutationObserver.observe(targetElement, config);
});

function syntaxHighlightCodeBlock(block) {
    if (!isElementVisibleOnScreen(block)) return;

    if (!currentlyGenerating) {
        hljs.highlightElement(block);
        block.setAttribute("data-highlighted", "true");
    }
    // While generating, highlight only the code block currently being added to
    else {
        const language = hljs.blockLanguage(block);
        if (language === "no-highlight")
            return

        const codeParagraphDivs = block.querySelectorAll("div");
         if (!codeParagraphDivs) {
            return;
        }

        // Highlight only the last two code paragraphs
        for (i = codeParagraphDivs.length - 1; i >= Math.max(0, codeParagraphDivs.length - 2); i--) {
            const div = codeParagraphDivs[i];
            if (div.hasAttribute("Skipped")) continue;
            // Skip highlighting second to last paragraph if its has already been highlighted
            if (i === codeParagraphDivs.length - 2 && div.querySelector("span")) break;

            const textContent = div.textContent;

            // For HTML blocks, auto-detect language of each code paragraph
            if (language === "html" || language === undefined) {
                const languageSubset = language === "html" ? ["html", "css", "javascript"] : null;
                highlightResult = hljs.highlightAuto(textContent, languageSubset);
                // Highlight JS may confuse short JavaScript paragraphs with CSS
                if (highlightResult.language === "css"
                    && highlightResult.secondBest && highlightResult.secondBest.language == "javascript")
                    highlightedCodeParagraph = highlightResult.secondBest.value;
                else
                    highlightedCodeParagraph = highlightResult.value;
            }
            // For all other languages, assume all paragraphs are the same language
            else
                highlightedCodeParagraph = hljs.highlight(textContent, { language, ignoreIllegals: true }).value;

            div.innerHTML = highlightedCodeParagraph;
        }

    }
    block.classList.add("pretty_scrollbar");
    // Scroll to bottom again if scroll position was previously at bottom
    if (block.shouldScroll) {
      block.scrollTop = block.scrollTopMax ? block.scrollTopMax : block.scrollHeight;
      block.shouldScroll = undefined;
    }
}

function syntaxHighlightKatex(container) {
    // Skip span elements inside code containers
    if (container.tagName === "SPAN"
        && (container.parentElement && container.parentElement.tagName === "DIV"
            || container.parentElement.parentElement && container.parentElement.parentElement.tagName === "DIV"))
        return;

    if (currentlyGenerating || isElementVisibleOnScreen(container)) {
        container.rawTextContent = container.textContent;
        renderMathInElement(container, {
            delimiters: [{
                    left: "$$",
                    right: "$$",
                    display: true
                },
                {
                    left: "$",
                    right: "$",
                    display: false
                },
                {
                    left: "\\(",
                    right: "\\)",
                    display: false
                },
                {
                    left: "\\[",
                    right: "\\]",
                    display: true
                },
            ],
        });
    }
}

function doSyntaxHighlighting(targetMessageBody = null) {
    const messageBodies = targetMessageBody ? [targetMessageBody] : document.getElementById("chat").querySelectorAll(".message-body");

    if (messageBodies.length > 0) {
        mutationObserver.disconnect();
        try {
          hasSeenVisible = false;

          // Go from last message to first
          for (let i = messageBodies.length - 1; i >= 0; i--) {
              const messageBody = messageBodies[i];

              intersectObserver.observe(messageBody);

              if (isElementVisibleOnScreen(messageBody)) {
                  hasSeenVisible = true;


                  //const mathContainers = messageBody.querySelectorAll("p, ol, ul, td, th, h1, h2, h3, h4, h5, h6, blockquote, figcaption,"
                  //                  + "caption, dd, dt")

                  // Only render math in direct descendants of messageBody (not, e.g., in thinking-/code blocks)
                  selectorString = ":scope > p, :scope > td, "
                                 + ":scope > th, :scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > h5, :scope > h6, "
                                 + ":scope > blockquote, :scope > figcaption, :scope > caption, :scope > dd, :scope > dt"
                  // TODO: this is necessary to ensure morphdom doesn't invalidate rendered (nested) lists
                  if (currentlyGenerating)
                    selectorString += ", :scope > ol, :scope > ul"
                  else
                    selectorString += ", li"
                  const mathContainers = messageBody.querySelectorAll(selectorString);
                  if (!mathContainers)
                      continue;

                  if (currentlyGenerating) {
                      for (j = mathContainers.length - 1; j >= 0; j--) {
                          container = mathContainers[j];
                          if (container.hasAttribute("Skipping"))
                            continue;

                          syntaxHighlightKatex(container);
                      }

                      const thinkingBlocks = messageBody.querySelectorAll("details");
                      thinkingBlocks.forEach(block => {
                        thinkingContent = block.children[1];
                        if (thinkingContent && thinkingContent.shouldScroll) {
                            thinkingContent.scrollTop = thinkingContent.scrollTopMax ? thinkingContent.scrollTopMax : thinkingContent.scrollHeight;
                            thinkingContent.shouldScroll = undefined;
                        }
                      });
                  }
                  else {
                      mathContainers.forEach(container => {
                          intersectObserver.observe(container);
                          syntaxHighlightKatex(container);
                      });
                  }

                  // Handle both code and math in a single pass through each message
                  const codeBlocks = messageBody.querySelectorAll("pre code:not([data-highlighted])");
                  codeBlocks.forEach((codeBlock) => {
                      intersectObserver.observe(codeBlock);
                      syntaxHighlightCodeBlock(codeBlock);
                  });
              } else if (hasSeenVisible) {
                  // We've seen visible messages but this one is not visible
                  // Since we're going from last to first, we can break
                  break;
              }
          }
        } finally {
            mutationObserver.observe(targetElement, config);
        }
    }
}

function handleMorphdomUpdate(data) {

  currentlyGenerating = !data.forceRender;

  // Determine target element and use it as query scope
  var target_element, target_html;
  if (data.last_message_only) {
    const childNodes = document.getElementById("chat-messages").childNodes;
    target_element = childNodes[childNodes.length - 2];
    target_html = data.html;
  } else {
    target_element = document.getElementById("chat").parentNode;
    target_html =  "<div class=\"prose svelte-1ybaih5\">" + data.html + "</div>";
  }

  const queryScope = target_element;

  // Track open blocks and store their scroll positions
  const openBlocks = new Set();
  const scrollPositions = {};
  queryScope.querySelectorAll(".thinking-block").forEach(block => {
    const blockId = block.getAttribute("data-block-id");
    if (blockId && block.hasAttribute("open")) {
      openBlocks.add(blockId);
      const content = block.querySelector(".thinking-content");
      if (content) {
        const scrollHeight = content.scrollHeight;
        if (content.scrollTopMax)
          isAtBottom = (content.scrollTopMax - content.scrollTop) < 5;
        else
          isAtBottom = (content.scrollHeight - content.clientHeight - content.scrollTop) < 5;
        scrollPositions[blockId] = {
          scrollHeight: scrollHeight,
          isAtBottom: isAtBottom
        };
      }
    }
  });

  // Store scroll positions for code blocks
  const codeScrollPositions = [];
  codeBlockIdx = 0;
  queryScope.querySelectorAll("code").forEach(block => {
    block.idx = codeBlockIdx;
    const scrollHeight = block.scrollHeight;
    if (block.scrollTopMax)
      isAtBottom = (block.scrollTopMax - block.scrollTop) < 5;
    else
      isAtBottom = (block.scrollHeight - block.clientHeight - block.scrollTop) < 5;
    codeScrollPositions.push({
      position: block.scrollTop,
      isAtBottom: isAtBottom
    });
    codeBlockIdx++;
  });

  morphdom(
    target_element,
    target_html,
    {
      onBeforeElUpdated: function(fromEl, toEl) {
        // Preserve code highlighting
         if (fromEl.tagName === "PRE") {
          const fromCode = fromEl.querySelector("code[data-highlighted]");
          const toCode = toEl.querySelector("code");

          if (fromCode && toCode && fromCode.textContent === toCode.textContent) {
            toEl.className = fromEl.className;
            toEl.innerHTML = fromEl.innerHTML;
            return false;
          }
        }

        // Preserve highlighted code paragraphs inside code blocks while generating
        if (!data.forceRender && fromEl.tagName === "DIV" && fromEl.parentElement.tagName === "CODE") {


          const toTextContent = toEl.textContent
          if (fromEl.textContent === toTextContent) {
            return false;
          }
        }

        // Preserve rendered KaTeX in math containers while generating
        if (!data.forceRender && fromEl.matches("p, li, td, th, h1, h2, h3, h4, h5, h6, blockquote, figcaption,"
                                                + "caption, dd, dt, div, ol, ul")) {
          const toTextContentTrimmed = toEl.textContent.trim();

          if (!toTextContentTrimmed || (fromEl.rawTextContent && fromEl.rawTextContent.trim().replace("\n", "") === toTextContentTrimmed.replace("\n", "")))
          {
            fromEl.setAttribute("Skipping", true);
            return false;
          }

        }

        if (data.forceRender && fromEl.matches("p, li, td, th, h1, h2, h3, h4, h5, h6, blockquote, figcaption,"
                                                + "caption, dd, dt, ol, ul"))
          fromEl.rawTextContent = null;

        // For thinking blocks, assume closed by default
        if (fromEl.classList && fromEl.classList.contains("thinking-block") &&
           toEl.classList && toEl.classList.contains("thinking-block")) {
          const blockId = toEl.getAttribute("data-block-id");
          if (toEl.getAttribute("data-streaming") === "true") {
            toEl.setAttribute("open", "");
          }
          // If this block was explicitly opened by user, keep it open
          if (blockId && openBlocks.has(blockId)) {
            toEl.setAttribute("open", "");
          }
        }

        return !fromEl.isEqualNode(toEl);
      },

      onElUpdated: function(el) {
        // Restore scroll positions for open thinking blocks
        if (el.classList && el.classList.contains("thinking-block") && el.hasAttribute("open")) {
          const blockId = el.getAttribute("data-block-id");
          const content = el.querySelector(".thinking-content");

          if (content && blockId && scrollPositions[blockId]) {
            setTimeout(() => {
              scrollHeight = content.scrollHeight;
              if (scrollPositions[blockId].isAtBottom && (scrollHeight > scrollPositions[blockId].scrollHeight)) {
                content.shouldScroll = true;
              } else {
                content.shouldScroll = undefined;
              }
            }, 0);
          }
        }
        // Restore scroll positions for code blocks
        if (el.tagName && el.tagName === "CODE") {
          const blockIdx = el.idx;
          if (blockIdx !== undefined && codeScrollPositions[blockIdx]) {
            setTimeout(() => {
              if (codeScrollPositions[blockIdx].isAtBottom) {
                el.shouldScroll = true;
              } else {
                el.shouldScroll = undefined;
              }
            }, 0);
          }
        }
      },
    }
  );

  // Add toggle listeners for new blocks
  queryScope.querySelectorAll(".thinking-block").forEach(block => {
    if (!block._hasToggleListener) {
      block.addEventListener("toggle", function(e) {
        if (this.open) {
          const content = this.querySelector(".thinking-content");
          if (content) {
            setTimeout(() => {
              content.scrollTop = content.scrollHeight;
            }, 0);
          }
        }
      });
      block._hasToggleListener = true;
    }
  });
  mutationObserver.disconnect();
  doSyntaxHighlighting();
  mutationObserver.observe(targetElement, config);
}
