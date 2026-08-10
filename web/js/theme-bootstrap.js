(function () {
    'use strict';

    const THEME_KEY = 'lightme_ui_theme';
    const ACCENT_KEY = 'lightme_ui_accent';
    const MOTION_KEY = 'lightme_ui_motion';

    function safeGet(key, fallback) {
        try {
            return localStorage.getItem(key) || fallback;
        } catch (_) {
            return fallback;
        }
    }

    function safeSet(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch (_) {
            // Private browsing or storage restrictions should not block the UI.
        }
    }

    function applyTheme(theme, persist) {
        const value = theme === 'light' ? 'light' : 'dark';
        document.documentElement.dataset.theme = value;
        if (persist !== false) safeSet(THEME_KEY, value);
        window.dispatchEvent(new CustomEvent('lightme:theme-change', { detail: { theme: value } }));
        return value;
    }

    function applyAccent(accent, persist) {
        const value = ['pink', 'cyan', 'violet'].includes(accent) ? accent : 'pink';
        document.documentElement.dataset.accent = value;
        if (persist !== false) safeSet(ACCENT_KEY, value);
        return value;
    }

    function applyMotion(enabled, persist) {
        document.documentElement.classList.toggle('motion-off', !enabled);
        if (persist !== false) safeSet(MOTION_KEY, enabled ? 'on' : 'off');
        return enabled;
    }

    const initialTheme = applyTheme(safeGet(THEME_KEY, 'dark'), false);
    applyAccent(safeGet(ACCENT_KEY, 'pink'), false);
    applyMotion(safeGet(MOTION_KEY, 'on') !== 'off', false);

    window.LightMeTheme = {
        get: () => document.documentElement.dataset.theme || initialTheme,
        set: (theme) => applyTheme(theme, true),
        toggle: () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark', true),
        setAccent: (accent) => applyAccent(accent, true),
        setMotion: (enabled) => applyMotion(Boolean(enabled), true),
        keys: { theme: THEME_KEY, accent: ACCENT_KEY, motion: MOTION_KEY }
    };

    document.addEventListener('DOMContentLoaded', () => {
        if (document.body.dataset.page === 'companion' || document.querySelector('[data-theme-control]')) return;

        const button = document.createElement('button');
        button.className = 'page-theme-toggle';
        button.type = 'button';
        button.setAttribute('aria-label', '切换浅色或深色主题');
        button.title = '切换浅色 / 深色';

        const render = () => {
            const dark = document.documentElement.dataset.theme === 'dark';
            button.innerHTML = `<span aria-hidden="true">${dark ? '☼' : '☾'}</span><small>${dark ? '浅色' : '深色'}</small>`;
        };

        button.addEventListener('click', () => {
            window.LightMeTheme.toggle();
            render();
        });
        render();
        document.body.appendChild(button);
    });
})();
