/**
 * CivikIndia Floating AI Chatbot Assistant
 */
document.addEventListener('DOMContentLoaded', function() {
    const triggerBtn = document.getElementById('chatbotTriggerBtn');
    const closeBtn = document.getElementById('chatbotCloseBtn');
    const windowEl = document.getElementById('chatbotWindow');
    const form = document.getElementById('chatbotForm');
    const input = document.getElementById('chatbotInput');
    const sendBtn = document.getElementById('chatbotSendBtn');
    const thread = document.getElementById('chatbotThread');
    const errorEl = document.getElementById('chatbotError');
    const unreadBadge = document.getElementById('chatbotUnreadBadge');
    const suggestions = document.getElementById('chatbotSuggestions');

    if (!triggerBtn || !closeBtn || !windowEl || !form || !input || !sendBtn || !thread || !errorEl) {
        return;
    }

    let isOpen = false;
    let pending = false;
    let typingIndicator = null;
    let welcomeShown = false;

    // Toggle Chat Window
    function toggleChat(forceState) {
        isOpen = (typeof forceState === 'boolean') ? forceState : !isOpen;
        if (isOpen) {
            windowEl.classList.remove('d-none');
            // Reset pop-in animation so it replays on every open
            windowEl.style.animation = 'none';
            // Force reflow to restart animation
            void windowEl.offsetWidth;
            windowEl.style.animation = '';
            windowEl.setAttribute('aria-hidden', 'false');
            input.focus();
            unreadBadge.classList.add('d-none');
            triggerBtn.classList.add('active');
            
            // Show initial greeting if not already shown
            if (!welcomeShown) {
                welcomeShown = true;
                setTimeout(() => {
                    appendMessage('assistant', 'Hello! I am your CivikIndia digital assistant. How can I help you use the portal today?');
                }, 400); // 400ms delay for smooth entrance sequence
            }
        } else {
            windowEl.classList.add('d-none');
            windowEl.setAttribute('aria-hidden', 'true');
            triggerBtn.classList.remove('active');
        }
    }

    triggerBtn.addEventListener('click', () => toggleChat());
    closeBtn.addEventListener('click', () => toggleChat(false));

    // Show initial unread badge after 4 seconds to guide the user
    setTimeout(() => {
        if (!isOpen) {
            unreadBadge.classList.remove('d-none');
            // Subtle pulse animation on trigger
            triggerBtn.style.transform = 'scale(1.1) rotate(-5deg)';
            setTimeout(() => {
                triggerBtn.style.transform = '';
            }, 500);
        }
    }, 4000);

    function setError(message) {
        if (!message) {
            errorEl.classList.add('d-none');
            errorEl.textContent = '';
            return;
        }
        errorEl.textContent = message;
        errorEl.classList.remove('d-none');
    }

    function appendMessage(role, text) {
        const messageWrap = document.createElement('div');
        const bubble = document.createElement('div');

        messageWrap.className = `chatbot-msg chatbot-msg-${role}`;
        bubble.className = 'chatbot-bubble';
        bubble.textContent = text;
        messageWrap.appendChild(bubble);
        thread.appendChild(messageWrap);

        // Trigger entry animation
        requestAnimationFrame(() => {
            messageWrap.classList.add('is-visible');
        });

        // Limit message history to prevent huge DOM size
        while (thread.children.length > 25) {
            thread.removeChild(thread.firstElementChild);
        }

        thread.scrollTo({
            top: thread.scrollHeight,
            behavior: 'smooth'
        });
    }

    function showTypingIndicator() {
        if (typingIndicator) return;

        typingIndicator = document.createElement('div');
        typingIndicator.className = 'chatbot-msg chatbot-msg-assistant chatbot-msg-typing is-visible';
        typingIndicator.innerHTML = `
            <div class="chatbot-bubble">
                <span class="chatbot-dot"></span>
                <span class="chatbot-dot"></span>
                <span class="chatbot-dot"></span>
            </div>
        `;
        thread.appendChild(typingIndicator);
        thread.scrollTo({
            top: thread.scrollHeight,
            behavior: 'smooth'
        });
    }

    function hideTypingIndicator() {
        if (!typingIndicator) return;
        typingIndicator.remove();
        typingIndicator = null;
    }

    function setPending(isPending) {
        pending = isPending;
        sendBtn.disabled = isPending;
        input.disabled = isPending;
        const suggestionBtns = suggestions.querySelectorAll('button');
        suggestionBtns.forEach(btn => btn.disabled = isPending);

        if (isPending) {
            showTypingIndicator();
        } else {
            hideTypingIndicator();
        }
    }

    async function askAssistant(text) {
        const promptText = (text || '').trim();
        if (promptText.length < 5) {
            setError('Please enter at least 5 characters.');
            input.focus();
            return;
        }
        if (pending) return;

        setError('');
        appendMessage('user', promptText);
        input.value = '';
        setPending(true);

        try {
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
            
            // Determine assistant mode depending on active page
            let assistantMode = 'homepage';
            const pageData = document.body.getAttribute('data-page') || '';
            if (pageData.includes('submit')) {
                assistantMode = 'draft';
            }

            const response = await fetch('/api/ai/assist', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({
                    assistant: assistantMode,
                    message: promptText
                })
            });

            let payload = {};
            try {
                payload = await response.json();
            } catch (e) {
                payload = {};
            }

            if (!response.ok) {
                setError(payload.error || 'Assistant is unavailable right now.');
                return;
            }

            const reply = (payload.reply || '').trim();
            appendMessage('assistant', reply || 'I could not generate a response. Please try again.');
        } catch (error) {
            console.error('Chatbot assistant error:', error);
            setError('Unable to connect to assistant. Please try again.');
        } finally {
            setPending(false);
        }
    }

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        askAssistant(input.value);
    });

    // Handle Quick Suggestions click
    suggestions.addEventListener('click', (e) => {
        const btn = e.target.closest('.btn-suggestion');
        if (!btn || pending) return;
        const prompt = btn.getAttribute('data-prompt') || '';
        askAssistant(prompt);
    });
});
