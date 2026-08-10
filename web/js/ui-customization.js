(function () {
    'use strict';

    const $ = (selector, root = document) => root.querySelector(selector);
    const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
    const BACKGROUND_KEY = 'lightme_ui_background';
    const CUSTOM_BACKGROUND_KEY = 'lightme_ui_custom_background';

    function safeGet(key, fallback = '') {
        try {
            return localStorage.getItem(key) || fallback;
        } catch (_) {
            return fallback;
        }
    }

    function safeSet(key, value) {
        try {
            localStorage.setItem(key, value);
            return true;
        } catch (_) {
            return false;
        }
    }

    function safeRemove(key) {
        try {
            localStorage.removeItem(key);
        } catch (_) {
            // Ignore unavailable storage.
        }
    }

    const overlay = $('#control-center-overlay');
    const panel = $('#control-center');
    const openButton = $('#control-center-btn');
    const closeButton = $('#control-center-close');
    const backdrop = $('#control-center-backdrop');
    const backgroundInput = $('#background-input');
    const backgroundUploadButton = $('#background-upload-btn');
    const backgroundStatus = $('#background-status');
    const motionToggle = $('#motion-toggle');
    let restoreFocusTarget = null;

    function renderThemeControls() {
        const current = document.documentElement.dataset.theme || 'dark';
        $$('.theme-option').forEach((button) => {
            const active = button.dataset.themeValue === current;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', String(active));
        });
    }

    function renderAccentControls() {
        const current = document.documentElement.dataset.accent || 'pink';
        $$('.accent-option').forEach((button) => {
            const active = button.dataset.accentValue === current;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', String(active));
        });
    }

    function applyBackground(value, customData) {
        let next = ['aurora', 'sakura', 'midnight', 'custom'].includes(value) ? value : 'aurora';
        const savedCustom = customData || safeGet(CUSTOM_BACKGROUND_KEY);

        if (next === 'custom' && !savedCustom) next = 'aurora';
        document.body.dataset.background = next;
        if (savedCustom) {
            document.body.style.setProperty('--custom-background', `url("${savedCustom}")`);
        }

        $$('.background-option').forEach((button) => {
            const active = button.dataset.backgroundValue === next;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', String(active));
        });
        safeSet(BACKGROUND_KEY, next);
        return next;
    }

    function setStatus(message, isError) {
        if (!backgroundStatus) return;
        backgroundStatus.textContent = message || '';
        backgroundStatus.style.color = isError ? 'var(--lm-danger)' : '';
    }

    function openControlCenter() {
        if (!overlay) return;
        restoreFocusTarget = document.activeElement;
        overlay.classList.add('open');
        overlay.setAttribute('aria-hidden', 'false');
        openButton.setAttribute('aria-expanded', 'true');
        window.setTimeout(() => closeButton.focus(), 90);
    }

    function closeControlCenter(restoreFocus = true) {
        if (!overlay) return;
        overlay.classList.remove('open');
        overlay.setAttribute('aria-hidden', 'true');
        openButton.setAttribute('aria-expanded', 'false');
        if (restoreFocus && restoreFocusTarget && typeof restoreFocusTarget.focus === 'function') {
            window.setTimeout(() => restoreFocusTarget.focus(), 40);
        }
    }

    function fitAndStoreBackground(file) {
        if (!file || !file.type.startsWith('image/')) {
            setStatus('请选择图片文件。', true);
            return;
        }
        if (file.size > 15 * 1024 * 1024) {
            setStatus('图片过大，请选择 15 MB 以内的图片。', true);
            return;
        }

        setStatus('正在准备你的专属背景…');
        const reader = new FileReader();
        reader.onerror = () => setStatus('图片读取失败，请换一张试试。', true);
        reader.onload = () => {
            const image = new Image();
            image.onerror = () => setStatus('暂时无法使用这张图片。', true);
            image.onload = () => {
                const maxSide = 1800;
                const scale = Math.min(1, maxSide / Math.max(image.naturalWidth, image.naturalHeight));
                const canvas = document.createElement('canvas');
                canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
                canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
                const context = canvas.getContext('2d');
                context.drawImage(image, 0, 0, canvas.width, canvas.height);
                const dataUrl = canvas.toDataURL('image/jpeg', .84);
                const persisted = safeSet(CUSTOM_BACKGROUND_KEY, dataUrl);
                applyBackground('custom', dataUrl);
                setStatus(persisted ? '专属背景已保存。' : '背景已应用，本次关闭页面后会恢复默认。', !persisted);
            };
            image.src = reader.result;
        };
        reader.readAsDataURL(file);
    }

    if (openButton) openButton.addEventListener('click', openControlCenter);
    if (closeButton) closeButton.addEventListener('click', () => closeControlCenter());
    if (backdrop) backdrop.addEventListener('click', () => closeControlCenter());

    $$('.theme-option').forEach((button) => {
        button.addEventListener('click', () => {
            window.LightMeTheme.set(button.dataset.themeValue);
            renderThemeControls();
        });
    });

    $$('.accent-option').forEach((button) => {
        button.addEventListener('click', () => {
            window.LightMeTheme.setAccent(button.dataset.accentValue);
            renderAccentControls();
        });
    });

    $$('.background-option').forEach((button) => {
        button.addEventListener('click', () => {
            const value = button.dataset.backgroundValue;
            if (value === 'custom' && !safeGet(CUSTOM_BACKGROUND_KEY)) {
                backgroundInput.click();
                return;
            }
            applyBackground(value);
            setStatus(value === 'custom' ? '已切换到你的专属背景。' : '背景已切换并保存。');
        });
    });

    if (backgroundUploadButton) backgroundUploadButton.addEventListener('click', () => backgroundInput.click());
    if (backgroundInput) {
        backgroundInput.addEventListener('change', () => {
            fitAndStoreBackground(backgroundInput.files && backgroundInput.files[0]);
            backgroundInput.value = '';
        });
    }

    if (motionToggle) {
        motionToggle.checked = !document.documentElement.classList.contains('motion-off');
        motionToggle.addEventListener('change', () => window.LightMeTheme.setMotion(motionToggle.checked));
    }

    const resetButton = $('#reset-appearance-btn');
    if (resetButton) {
        resetButton.addEventListener('click', () => {
            safeRemove(BACKGROUND_KEY);
            safeRemove(CUSTOM_BACKGROUND_KEY);
            window.LightMeTheme.set('dark');
            window.LightMeTheme.setAccent('pink');
            window.LightMeTheme.setMotion(true);
            if (motionToggle) motionToggle.checked = true;
            document.body.style.removeProperty('--custom-background');
            applyBackground('aurora');
            renderThemeControls();
            renderAccentControls();
            setStatus('已恢复 LightMe 默认外观。');
        });
    }

    document.addEventListener('keydown', (event) => {
        const commandKey = event.ctrlKey || event.metaKey;
        if (commandKey && event.key.toLowerCase() === 'k') {
            event.preventDefault();
            overlay.classList.contains('open') ? closeControlCenter() : openControlCenter();
        }
        if (commandKey && event.key.toLowerCase() === 'n') {
            event.preventDefault();
            $('#new-chat-btn')?.click();
        }
        if (event.key === 'Escape' && overlay.classList.contains('open')) {
            event.preventDefault();
            closeControlCenter();
        }
        if (event.key === 'Tab' && overlay.classList.contains('open')) {
            const focusable = $$('button:not([disabled]), input:not([disabled]), select:not([disabled])', panel)
                .filter((element) => element.offsetParent !== null);
            if (!focusable.length) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
    });

    document.addEventListener('pointerdown', (event) => {
        const target = event.target.closest('button:not(:disabled), .session-item');
        if (!target || document.documentElement.classList.contains('motion-off')) return;
        const rect = target.getBoundingClientRect();
        target.style.setProperty('--ripple-x', `${event.clientX - rect.left}px`);
        target.style.setProperty('--ripple-y', `${event.clientY - rect.top}px`);
        target.classList.remove('is-rippling');
        void target.offsetWidth;
        target.classList.add('is-rippling');
        window.setTimeout(() => target.classList.remove('is-rippling'), 560);
        if (event.pointerType === 'touch' && navigator.vibrate) navigator.vibrate(8);
    }, { passive: true });

    ['#tools-btn', '#workflow-btn', '#term-btn', '#config-btn', '#companion-btn', '#desktop-pet-btn'].forEach((selector) => {
        $(selector)?.addEventListener('click', () => window.setTimeout(() => closeControlCenter(false), 0));
    });

    window.addEventListener('lightme:theme-change', renderThemeControls);
    applyBackground(safeGet(BACKGROUND_KEY, 'aurora'));
    renderThemeControls();
    renderAccentControls();
})();
