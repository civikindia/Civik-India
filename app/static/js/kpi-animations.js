/**
 * Civik India — KPI Animations Module
 * Handles count-up transitions, trend calculations, and Canvas-based sparklines.
 */
(function() {
    'use strict';

    // Ease-out quad formula
    function easeOutQuad(t) {
        return t * (2 - t);
    }

    /**
     * Animates a counter from its current text value to a target value.
     */
    function animateCounter(el, targetVal, duration = 1000) {
        if (!el) return;
        
        const cleanStr = (el.textContent || '0').replace(/[^0-9.]/g, '');
        const startVal = parseFloat(cleanStr) || 0;
        const safeTarget = parseFloat(targetVal) || 0;
        
        if (startVal === safeTarget) {
            el.textContent = targetVal.toString();
            return;
        }

        const isPercent = targetVal.toString().includes('%') || (el.textContent || '').includes('%');
        
        // Detect decimal places
        const targetStr = targetVal.toString();
        const decimalMatch = targetStr.match(/\.(\d+)/);
        const decimals = decimalMatch ? decimalMatch[1].length : 0;

        const startTs = performance.now();

        function step(ts) {
            const progress = Math.min((ts - startTs) / duration, 1);
            const eased = easeOutQuad(progress);
            const current = startVal + (safeTarget - startVal) * eased;

            let displayVal = current.toFixed(decimals);
            if (isPercent) {
                displayVal += '%';
            }
            el.textContent = displayVal;

            if (progress < 1) {
                requestAnimationFrame(step);
            } else {
                el.textContent = targetVal.toString();
            }
        }

        requestAnimationFrame(step);
    }

    /**
     * Calculates and renders a trend badge (▲/▼) comparing current and previous values.
     */
    function animateTrendBadge(el, currentVal, previousVal) {
        if (!el) return;

        const curr = parseFloat(currentVal) || 0;
        const prev = parseFloat(previousVal) || 0;

        el.className = 'kpi-trend-badge';
        
        if (prev === 0) {
            if (curr > 0) {
                el.innerHTML = '<i class="bi bi-arrow-up-short"></i> New';
                el.classList.add('kpi-trend-up');
            } else {
                el.innerHTML = '—';
                el.classList.add('kpi-trend-flat');
            }
            return;
        }

        const pctChange = ((curr - prev) / prev) * 100;
        const absChange = Math.abs(pctChange).toFixed(1);

        if (pctChange > 0.5) {
            el.innerHTML = `<i class="bi bi-arrow-up-short"></i> ${absChange}%`;
            el.classList.add('kpi-trend-up');
        } else if (pctChange < -0.5) {
            el.innerHTML = `<i class="bi bi-arrow-down-short"></i> ${absChange}%`;
            el.classList.add('kpi-trend-down');
        } else {
            el.innerHTML = '—';
            el.classList.add('kpi-trend-flat');
        }
    }

    /**
     * Draws a minimal, stylish sparkline on a canvas element using Canvas 2D.
     */
    function drawSparkline(canvas, dataPoints, strokeColor = '#6366f1') {
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const points = (dataPoints || []).map(Number);
        if (points.length < 2) {
            canvas.style.display = 'none';
            return;
        }
        canvas.style.display = 'inline-block';

        // Set device pixel ratio scale for crisp lines
        const dpr = window.devicePixelRatio || 1;
        const rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        ctx.scale(dpr, dpr);

        const width = rect.width;
        const height = rect.height;

        ctx.clearRect(0, 0, width, height);

        const maxVal = Math.max(...points, 1);
        const minVal = Math.min(...points, 0);
        const range = maxVal - minVal;

        // Path drawing
        ctx.beginPath();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = strokeColor;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';

        const stepX = width / (points.length - 1);
        points.forEach((val, idx) => {
            const x = idx * stepX;
            // Leave 3px padding top and bottom
            const y = height - 3 - ((val - minVal) / range) * (height - 6);
            if (idx === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        });
        ctx.stroke();

        // Under-fill gradient
        ctx.lineTo((points.length - 1) * stepX, height);
        ctx.lineTo(0, height);
        ctx.closePath();
        
        const grad = ctx.createLinearGradient(0, 0, 0, height);
        // Make the color semi-transparent
        let fillStyle = 'rgba(99, 102, 241, 0.08)';
        if (strokeColor.startsWith('#')) {
            fillStyle = strokeColor + '14'; // ~8% opacity in hex
        }
        grad.addColorStop(0, fillStyle);
        grad.addColorStop(1, 'rgba(255, 255, 255, 0)');
        ctx.fillStyle = grad;
        ctx.fill();
    }

    // Export module
    window.KPIAnimations = {
        animateCounter,
        animateTrendBadge,
        drawSparkline
    };
})();
