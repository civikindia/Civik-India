/**
 * CivikIndia — Civic Grievance & Accountability Portal
 * Main JavaScript File
 */

document.addEventListener('DOMContentLoaded', function() {
    function safeRun(name, fn) {
        try {
            fn();
        } catch (err) {
            console.error('[CivikIndia]', name, err);
        }
    }

    safeRun('initTooltips', initTooltips);
    safeRun('initPopovers', initPopovers);
    safeRun('autoHideAlerts', autoHideAlerts);
    safeRun('confirmDangerousActions', confirmDangerousActions);
    safeRun('initCharCounters', initCharCounters);
    safeRun('initStaggeredLists', initStaggeredLists);
    safeRun('initRevealAnimations', initRevealAnimations);
    safeRun('initCounterAnimations', initCounterAnimations);
    safeRun('enhanceFormSubmitFeedback', enhanceFormSubmitFeedback);
    safeRun('initHomeAiChatbot', initHomeAiChatbot);
    safeRun('initAccessibilityControls', initAccessibilityControls);
});

/**
 * Initialize Bootstrap tooltips
 */
function initTooltips() {
    const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
    tooltipTriggerList.forEach(function(tooltipTriggerEl) {
        new bootstrap.Tooltip(tooltipTriggerEl);
    });
}

/**
 * Initialize Bootstrap popovers
 */
function initPopovers() {
    const popoverTriggerList = document.querySelectorAll('[data-bs-toggle="popover"]');
    popoverTriggerList.forEach(function(popoverTriggerEl) {
        new bootstrap.Popover(popoverTriggerEl);
    });
}

/**
 * Auto-hide alert messages after 5 seconds
 */
function autoHideAlerts() {
    const alerts = document.querySelectorAll('.alert:not(.alert-permanent)');
    alerts.forEach(function(alert) {
        setTimeout(function() {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });
}

/**
 * Add confirmation dialog to dangerous actions
 */
function confirmDangerousActions() {
    const dangerButtons = document.querySelectorAll('[data-confirm]');
    dangerButtons.forEach(function(button) {
        button.addEventListener('click', function(e) {
            const message = this.getAttribute('data-confirm') || 'Are you sure?';
            if (!confirm(message)) {
                e.preventDefault();
                return false;
            }
        });
    });
}

/**
 * Initialize character counters for textareas
 */
function initCharCounters() {
    const textareas = document.querySelectorAll('textarea[maxlength]');
    textareas.forEach(function(textarea) {
        const maxLength = textarea.getAttribute('maxlength');
        const counterId = textarea.id + '_counter';
        
        // Create counter element if it doesn't exist
        let counter = document.getElementById(counterId);
        if (!counter) {
            counter = document.createElement('small');
            counter.id = counterId;
            counter.className = 'text-muted';
            textarea.parentNode.appendChild(counter);
        }
        
        // Update counter
        function updateCounter() {
            const current = textarea.value.length;
            counter.textContent = current + ' / ' + maxLength + ' characters';
            
            if (current > maxLength * 0.9) {
                counter.classList.add('text-warning');
            } else {
                counter.classList.remove('text-warning');
            }
        }
        
        textarea.addEventListener('input', updateCounter);
        updateCounter(); // Initial update
    });
}

/**
 * Reveal blocks as they enter viewport.
 */
function initRevealAnimations() {
    const revealItems = document.querySelectorAll('[data-reveal], [data-reveal-dir]');
    if (!revealItems.length) {
        return;
    }

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (prefersReducedMotion || !('IntersectionObserver' in window)) {
        revealItems.forEach(function(item) {
            item.classList.add('is-visible');
        });
        return;
    }

    const observer = new IntersectionObserver(function(entries, obs) {
        entries.forEach(function(entry) {
            if (!entry.isIntersecting) {
                return;
            }

            const item = entry.target;
            item.classList.add('is-visible');
            obs.unobserve(item);
        });
    }, {
        threshold: 0.15,
        rootMargin: '0px 0px -32px 0px'
    });

    revealItems.forEach(function(item, index) {
        // Read delay attribute or fall back to sequence calculation
        const customDelay = item.getAttribute('data-reveal-delay');
        if (customDelay !== null) {
            item.style.transitionDelay = `${customDelay}ms`;
        } else if (!item.style.transitionDelay) {
            item.style.transitionDelay = `${Math.min(index * 40, 220)}ms`;
        }
        observer.observe(item);
    });
}

/**
 * Initialize staggered lists.
 */
function initStaggeredLists() {
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (prefersReducedMotion) {
        return;
    }

    const lists = document.querySelectorAll('.how-steps, .report-grid, .admin-inbox-grid, .quick-actions-grid, .mini-metrics, #complaintsList');
    lists.forEach(function(list) {
        const children = list.querySelectorAll(':scope > article, :scope > .report-card, :scope > a, :scope > div, :scope > .col-12');
        children.forEach(function(child, index) {
            child.style.transitionDelay = `${index * 80}ms`;
        });
    });
}

/**
 * Initialize counter count-up animations.
 */
function initCounterAnimations() {
    const counterItems = document.querySelectorAll('.counter-value, [data-count]');
    if (!counterItems.length) {
        return;
    }

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (prefersReducedMotion) {
        counterItems.forEach(function(item) {
            const targetStr = item.getAttribute('data-count') || item.textContent;
            item.textContent = targetStr;
        });
        return;
    }

    // Quad ease out formula: t * (2 - t)
    function easeOutQuad(t) {
        return t * (2 - t);
    }

    const observer = new IntersectionObserver(function(entries, obs) {
        entries.forEach(function(entry) {
            if (!entry.isIntersecting) {
                return;
            }

            const item = entry.target;
            obs.unobserve(item);

            const targetStr = item.getAttribute('data-count');
            if (!targetStr) return;

            // Parse numbers, preserving format
            const isPercent = targetStr.includes('%') || item.textContent.includes('%');
            const cleanStr = targetStr.replace(/[^0-9.]/g, '');
            const targetVal = parseFloat(cleanStr);
            if (isNaN(targetVal)) {
                item.textContent = targetStr;
                return;
            }

            // Detect decimal places
            const decimalMatch = cleanStr.match(/\.(\d+)/);
            const decimals = decimalMatch ? decimalMatch[1].length : 0;

            const duration = 2000; // 2 seconds
            let startTime = null;

            function animate(timestamp) {
                if (!startTime) startTime = timestamp;
                const progress = timestamp - startTime;
                const percent = Math.min(progress / duration, 1);
                const easedPercent = easeOutQuad(percent);
                const currentVal = easedPercent * targetVal;

                let displayVal = currentVal.toFixed(decimals);
                if (isPercent) {
                    displayVal += '%';
                }

                item.textContent = displayVal;

                if (percent < 1) {
                    requestAnimationFrame(animate);
                } else {
                    item.textContent = targetStr; // ensure exact value at end
                }
            }

            requestAnimationFrame(animate);
        });
    }, {
        threshold: 0.1,
        rootMargin: '0px 0px -10px 0px'
    });

    counterItems.forEach(function(item) {
        observer.observe(item);
    });
}

/**
 * Add submit loading state to forms that do not already handle it.
 */
function enhanceFormSubmitFeedback() {
    const forms = document.querySelectorAll('form');
    forms.forEach(function(form) {
        if (form.dataset.loadingBound === 'true') {
            return;
        }

        form.dataset.loadingBound = 'true';
        form.addEventListener('submit', function(event) {
            const submitBtn = form.querySelector('button[type="submit"]:not([data-no-loading])');
            if (!submitBtn || submitBtn.disabled) {
                return;
            }

            setTimeout(function() {
                if (event.defaultPrevented || submitBtn.disabled) {
                    return;
                }
                submitBtn.disabled = true;
                submitBtn.dataset.originalText = submitBtn.innerHTML;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Processing...';
            }, 0);
        });
    });
}

/**
 * Initialize homepage AI chatbot.
 */
function initHomeAiChatbot() {
    const chatRoot = document.getElementById('homeAiChat');
    if (!chatRoot) {
        return;
    }

    const thread = document.getElementById('homeAiThread');
    const form = document.getElementById('homeAiForm');
    const input = document.getElementById('homeAiInput');
    const sendBtn = document.getElementById('homeAiSend');
    const errorEl = document.getElementById('homeAiError');
    const promptButtons = chatRoot.querySelectorAll('[data-ai-prompt]');

    if (!thread || !form || !input || !sendBtn || !errorEl) {
        return;
    }

    const defaultSendHtml = sendBtn.innerHTML;
    let pending = false;
    let typingIndicator = null;

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

        messageWrap.className = `home-ai-msg home-ai-msg-${role}`;
        bubble.className = 'home-ai-bubble';
        bubble.textContent = text;
        messageWrap.appendChild(bubble);
        thread.appendChild(messageWrap);

        // Trigger entry animation.
        requestAnimationFrame(function() {
            messageWrap.classList.add('is-visible');
        });

        while (thread.children.length > 16) {
            thread.removeChild(thread.firstElementChild);
        }

        thread.scrollTo({
            top: thread.scrollHeight,
            behavior: 'smooth'
        });
    }

    function showTypingIndicator() {
        if (typingIndicator) {
            return;
        }

        typingIndicator = document.createElement('div');
        typingIndicator.className = 'home-ai-msg home-ai-msg-assistant home-ai-msg-typing is-visible';
        typingIndicator.innerHTML = `
            <div class="home-ai-bubble">
                <span class="home-ai-dot"></span>
                <span class="home-ai-dot"></span>
                <span class="home-ai-dot"></span>
            </div>
        `;
        thread.appendChild(typingIndicator);
        thread.scrollTo({
            top: thread.scrollHeight,
            behavior: 'smooth'
        });
    }

    function hideTypingIndicator() {
        if (!typingIndicator) {
            return;
        }
        typingIndicator.remove();
        typingIndicator = null;
    }

    function setPending(isPending) {
        pending = isPending;
        sendBtn.disabled = isPending;
        input.disabled = isPending;
        promptButtons.forEach(function(btn) {
            btn.disabled = isPending;
        });

        if (isPending) {
            sendBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Thinking';
            showTypingIndicator();
        } else {
            sendBtn.innerHTML = defaultSendHtml;
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
        if (pending) {
            return;
        }

        setError('');
        appendMessage('user', promptText);
        input.value = '';
        setPending(true);

        try {
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
            const response = await fetch('/api/ai/assist', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({
                    assistant: 'homepage',
                    message: promptText
                })
            });

            let payload = {};
            try {
                payload = await response.json();
            } catch (error) {
                payload = {};
            }

            if (!response.ok) {
                setError(payload.error || 'AI assistant is unavailable right now.');
                return;
            }

            const reply = (payload.reply || '').trim();
            appendMessage('assistant', reply || 'I could not generate a response. Please try again.');
        } catch (error) {
            console.error('Homepage AI assistant error:', error);
            setError('Unable to connect to AI assistant. Please try again.');
        } finally {
            setPending(false);
        }
    }

    form.addEventListener('submit', function(event) {
        event.preventDefault();
        askAssistant(input.value);
    });

    promptButtons.forEach(function(btn) {
        btn.addEventListener('click', function() {
            const quickPrompt = btn.getAttribute('data-ai-prompt') || '';
            askAssistant(quickPrompt);
        });
    });

    appendMessage(
        'assistant',
        'I can help you submit complaints, track status, and find the right portal page. Ask your question to begin.'
    );
}

/**
 * Copy text to clipboard
 * @param {string} text - Text to copy
 * @param {HTMLElement} button - Button element for feedback
 */
function copyToClipboard(text, button) {
    navigator.clipboard.writeText(text).then(function() {
        // Show success feedback
        const originalText = button.innerHTML;
        button.innerHTML = '<i class="bi bi-check me-2"></i>Copied!';
        button.classList.add('btn-success');
        button.classList.remove('btn-outline-primary');
        
        setTimeout(function() {
            button.innerHTML = originalText;
            button.classList.remove('btn-success');
            button.classList.add('btn-outline-primary');
        }, 2000);
    }).catch(function(err) {
        console.error('Failed to copy:', err);
        alert('Failed to copy to clipboard. Please copy manually.');
    });
}

/**
 * Format date for display
 * @param {Date} date - Date object
 * @param {string} format - Format string
 * @returns {string} Formatted date
 */
function formatDate(date, format = 'DD MMM YYYY') {
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    
    const d = new Date(date);
    const day = String(d.getDate()).padStart(2, '0');
    const month = months[d.getMonth()];
    const year = d.getFullYear();
    
    return format
        .replace('DD', day)
        .replace('MMM', month)
        .replace('YYYY', year);
}

/**
 * Debounce function
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in milliseconds
 * @returns {Function} Debounced function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Show loading spinner on form submit
 * @param {HTMLFormElement} form - Form element
 */
function showFormLoading(form) {
    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.dataset.originalText = submitBtn.innerHTML;
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Processing...';
    }
}

/**
 * Hide loading spinner on form
 * @param {HTMLFormElement} form - Form element
 */
function hideFormLoading(form) {
    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn && submitBtn.dataset.originalText) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = submitBtn.dataset.originalText;
    }
}

/**
 * Validate email format
 * @param {string} email - Email to validate
 * @returns {boolean} Is valid
 */
function isValidEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

/**
 * Validate tracking ID format
 * @param {string} trackingId - Tracking ID to validate
 * @returns {boolean} Is valid
 */
function isValidTrackingId(trackingId) {
    if (!trackingId || trackingId.length !== 11) {
        return false;
    }
    if (!trackingId.startsWith('MIB')) {
        return false;
    }
    const randomPart = trackingId.substring(3);
    return /^[A-Z0-9]+$/.test(randomPart);
}

/**
 * AJAX helper function
 * @param {string} url - URL to fetch
 * @param {Object} options - Fetch options
 * @returns {Promise} Fetch promise
 */
async function ajax(url, options = {}) {
    const defaultOptions = {
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    };
    
    // Add CSRF token for non-GET requests
    if (options.method && options.method !== 'GET') {
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
        if (csrfToken) {
            defaultOptions.headers['X-CSRFToken'] = csrfToken;
        }
    }
    
    const mergedOptions = { ...defaultOptions, ...options };
    
    try {
        const response = await fetch(url, mergedOptions);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('AJAX Error:', error);
        throw error;
    }
}

// Export functions for global access
window.MIBSP = {
    copyToClipboard,
    formatDate,
    debounce,
    showFormLoading,
    hideFormLoading,
    isValidEmail,
    isValidTrackingId,
    ajax,
    initRevealAnimations,
    initStaggeredLists,
    initCounterAnimations,
    initHomeAiChatbot,
    initAccessibilityControls
};

/**
 * Initialize GIGW Accessibility Controls (Font size scaling & Contrast mode)
 */
function initAccessibilityControls() {
    const htmlEl = document.documentElement;
    
    // 1. Font Size Scaling
    const fontScaleBtns = document.querySelectorAll('.font-scale-btn');
    const savedScale = localStorage.getItem('civikindia-font-scale') || 'medium';
    
    function applyFontScale(scale) {
        htmlEl.setAttribute('data-font-scale', scale);
        localStorage.setItem('civikindia-font-scale', scale);
        
        fontScaleBtns.forEach(function(btn) {
            if (btn.getAttribute('data-scale') === scale) {
                btn.classList.add('active');
                btn.setAttribute('aria-pressed', 'true');
            } else {
                btn.classList.remove('active');
                btn.setAttribute('aria-pressed', 'false');
            }
        });
    }
    
    fontScaleBtns.forEach(function(btn) {
        btn.addEventListener('click', function() {
            const scale = this.getAttribute('data-scale');
            applyFontScale(scale);
        });
    });
    
    // Initial font scale load
    applyFontScale(savedScale);

    // 2. High Contrast Theme
    const contrastBtn = document.getElementById('contrastToggleBtn');
    const savedTheme = localStorage.getItem('civikindia-theme') || 'normal';
        function applyTheme(theme) {
        if (theme === 'contrast') {
            htmlEl.setAttribute('data-theme', 'contrast');
            localStorage.setItem('civikindia-theme', 'contrast');
            if (contrastBtn) {
                contrastBtn.classList.add('active');
                contrastBtn.setAttribute('aria-pressed', 'true');
            }
        } else {
            htmlEl.removeAttribute('data-theme');
            localStorage.setItem('civikindia-theme', 'normal');
            if (contrastBtn) {
                contrastBtn.classList.remove('active');
                contrastBtn.setAttribute('aria-pressed', 'false');
            }
        }
        window.dispatchEvent(new CustomEvent('themechanged', { detail: { theme: theme } }));
    }

    if (contrastBtn) {
        contrastBtn.addEventListener('click', function() {
            const currentTheme = htmlEl.getAttribute('data-theme') === 'contrast' ? 'normal' : 'contrast';
            applyTheme(currentTheme);
        });
    }
    
    // Initial theme load
    applyTheme(savedTheme);
}
