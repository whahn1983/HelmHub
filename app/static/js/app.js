(() => {
  'use strict';

  const doc = document;
  const body = doc.body;
  const $ = (sel, root = doc) => root.querySelector(sel);
  const $$ = (sel, root = doc) => Array.from(root.querySelectorAll(sel));

  const THEME_KEY = 'helmhub_theme';

  const ThemeManager = {
    media: window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null,

    getStoredTheme() {
      return localStorage.getItem(THEME_KEY);
    },

    getBodyTheme() {
      const dataTheme = body?.dataset?.theme;
      if (dataTheme) return dataTheme;
      const m = (body.className || '').match(/theme-(light|dark|system)/);
      return m ? m[1] : null;
    },

    getTheme() {
      const stored = this.getStoredTheme();
      if (stored) return stored;
      return this.getBodyTheme() || 'system';
    },

    resolveTheme(theme) {
      if (theme === 'dark') return 'dark';
      if (theme === 'light') return 'light';
      if (this.media) return this.media.matches ? 'dark' : 'light';
      return 'light';
    },

    applyTheme(theme = this.getTheme()) {
      const effective = this.resolveTheme(theme);
      body.classList.remove('theme-light', 'theme-dark', 'theme-system');
      body.classList.add(`theme-${theme}`);
      if (theme === 'system') {
        body.classList.add(`theme-${effective}`);
      }
      body.dataset.theme = theme;
      const metaTheme = $('meta[name="theme-color"]');
      if (metaTheme) {
        metaTheme.setAttribute('content', effective === 'dark' ? '#0f0f1a' : '#1a1a2e');
      }
    },

    setTheme(theme) {
      localStorage.setItem(THEME_KEY, theme);
      this.applyTheme(theme);
    },

    init() {
      this.applyTheme();
      if (this.media && typeof this.media.addEventListener === 'function') {
        this.media.addEventListener('change', () => {
          if (this.getTheme() === 'system') this.applyTheme('system');
        });
      }
      $$('[data-theme-toggle]').forEach((el) => {
        el.addEventListener('click', () => {
          const next = el.getAttribute('data-theme-toggle');
          if (next) this.setTheme(next);
        });
      });
    }
  };

  function formatTime(date, use12) {
    let h = date.getHours();
    const m = String(date.getMinutes()).padStart(2, '0');
    if (use12) {
      const ampm = h >= 12 ? 'PM' : 'AM';
      h = h % 12 || 12;
      return `${h}:${m} ${ampm}`;
    }
    return `${String(h).padStart(2, '0')}:${m}`;
  }

  function updateClock() {
    const now = new Date();
    $$('.live-time, #live-clock, .current-time').forEach((el) => {
      const use12 = (el.dataset.format || '').trim() === '12';
      el.textContent = formatTime(now, use12);
    });

    const focusClock = $('#focus-clock');
    if (focusClock) {
      const use12 = (focusClock.dataset.format || '').trim() === '12' || !!$('#focus-ampm');
      const t = formatTime(now, use12);
      if (use12) {
        const [hm, ampm] = t.split(' ');
        focusClock.textContent = hm;
        const ampmEl = $('#focus-ampm');
        if (ampmEl) ampmEl.textContent = ampm || '';
      } else {
        focusClock.textContent = t;
      }
    }
  }

  function updateGreeting() {
    const el = $('.greeting-text, #greeting-text, .greeting');
    if (!el) return;
    const hour = new Date().getHours();
    let greeting = 'Hello';
    if (hour < 5) greeting = 'Good night';
    else if (hour < 12) greeting = 'Good morning';
    else if (hour < 18) greeting = 'Good afternoon';
    else greeting = 'Good evening';

    const current = (el.textContent || '').trim();
    const replaced = current.replace(/^(Good\s(morning|afternoon|evening|night)|Hello),?/i, greeting);
    el.textContent = replaced === current ? `${greeting}` : replaced;
  }



  const EntityFormModal = {
    overlay: null,
    frame: null,
    title: null,
    _closing: false,

    init() {
      this.overlay = $('#entity-form-overlay');
      this.frame = $('#entity-form-frame');
      this.title = $('#entity-form-title');
      if (!this.overlay || !this.frame) return;

      doc.addEventListener('click', (event) => {
        const trigger = event.target.closest('[data-modal-form]');
        if (!trigger) return;
        event.preventDefault();
        const href = trigger.getAttribute('href');
        const modalTitle = trigger.getAttribute('data-modal-title') || trigger.textContent.trim() || 'Add Item';
        this.open(href, modalTitle);
      });

      this.overlay.addEventListener('click', (event) => {
        if (event.target === this.overlay) this.close();
      });

      this.frame.addEventListener('load', () => {
        // Guard: ignore the load event we triggered ourselves by setting src='about:blank'
        if (this._closing) return;
        try {
          const framePath = this.frame.contentWindow?.location?.pathname || '';
          if (!framePath.endsWith('/new') && !framePath.includes('/edit')) {
            this.close(true);
          }
        } catch (err) {
          // Ignore cross-origin/frame timing issues.
        }
      });

      window.addEventListener('message', (event) => {
        if (event.data === 'helmhub:close-entity-modal') {
          this.close(true);
        } else if (event.data === 'helmhub:cancel-entity-modal') {
          this.close(false);
        }
      });
    },

    open(url, title) {
      if (!url || !this.overlay || !this.frame) return;
      this._closing = false;
      if (this.title) this.title.textContent = title;
      this.frame.src = url;
      this.overlay.hidden = false;
      body.classList.add('modal-open');
    },

    close(refresh = false) {
      if (!this.overlay || !this.frame) return;
      this._closing = true;
      this.overlay.hidden = true;
      this.frame.src = 'about:blank';
      body.classList.remove('modal-open');
      if (refresh) window.location.reload();
    }
  };

  const QuickCapture = {
    overlay: null,

    getPreferredType() {
      const path = window.location.pathname || '';
      if (path.startsWith('/notes')) return 'note';
      if (path.startsWith('/bookmarks')) return 'bookmark';
      if (path.startsWith('/reminders')) return 'reminder';
      if (path.startsWith('/events')) return 'event';
      return 'task';
    },

    init() {
      this.overlay = $('#quick-capture-overlay');
      const fab = $('#fab-btn, .fab');
      if (fab) {
        fab.addEventListener('click', (e) => {
          e.preventDefault();
          this.open(this.getPreferredType());
        });
      }

      if (this.overlay) {
        this.overlay.addEventListener('click', (e) => {
          if (e.target === this.overlay) this.close();
        });
      }
    },

    open(type = 'task') {
      if (!this.overlay) return;
      this.overlay.hidden = false;
      body.classList.add('modal-open');
      this.switchTab(type);
      const input = this.overlay.querySelector('.tab-panel.active input:not([type="hidden"]), .tab-panel.active textarea');
      if (input) input.focus();
    },

    close() {
      if (!this.overlay) return;
      this.overlay.hidden = true;
      body.classList.remove('modal-open');
    },

    switchTab(type = 'task') {
      $$('.modal-tab').forEach((btn) => {
        const active = btn.id === `tab-btn-${type}`;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', String(active));
      });

      $$('.tab-panel').forEach((panel) => {
        const active = panel.id === `tab-${type}`;
        panel.classList.toggle('active', active);
        panel.hidden = !active;
      });
    }
  };

  function initFlashMessages() {
    $$('.flash').forEach((flash) => {
      window.setTimeout(() => {
        flash.style.opacity = '0';
        flash.style.transform = 'translateY(-8px)';
        window.setTimeout(() => flash.remove(), 260);
      }, 3000);
    });
  }

  function initHTMXHandlers() {
    doc.addEventListener('htmx:configRequest', (event) => {
      const tokenInput = $('#csrf-global, input[name="csrf_token"]');
      const csrfToken = tokenInput?.value;
      if (!csrfToken) return;

      const { detail } = event;
      if (!detail.parameters?.csrf_token) {
        detail.parameters = detail.parameters || {};
        detail.parameters.csrf_token = csrfToken;
      }
      detail.headers = detail.headers || {};
      detail.headers['X-CSRFToken'] = csrfToken;
    });

    doc.addEventListener('htmx:afterRequest', (event) => {
      const { xhr, target } = event.detail || {};
      const status = xhr?.status || 0;

      if (status >= 200 && status < 300) {
        if (target && target.id === 'quick-capture-result') {
          window.setTimeout(() => QuickCapture.close(), 600);
        }

        const existing = $('#save-result-indicator');
        const indicator = existing || doc.createElement('span');
        indicator.id = 'save-result-indicator';
        indicator.className = 'save-result-indicator save-success';
        indicator.textContent = 'Saved';

        const actions = $('.form-actions, .settings-save-row, .page-header-actions') || body;
        actions.appendChild(indicator);
        window.setTimeout(() => indicator.remove(), 1200);
      }

      if (target && target.firstElementChild) {
        const candidate = target.firstElementChild;
        if (candidate.classList.contains('task-item') || candidate.classList.contains('note-card') || candidate.classList.contains('event-item')) {
          candidate.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      }
    });
  }

  let deferredPrompt = null;

  function initPWABanner() {
    const banner = $('#install-banner, #pwa-banner');
    const installBtn = $('#install-btn, #pwa-install-btn');

    window.addEventListener('beforeinstallprompt', (e) => {
      e.preventDefault();
      deferredPrompt = e;
      const isStandalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
      if (banner && !localStorage.getItem('pwa-dismissed') && !isStandalone) banner.hidden = false;
    });

    if (installBtn) {
      installBtn.addEventListener('click', async () => {
        if (!deferredPrompt) return;
        deferredPrompt.prompt();
        try {
          await deferredPrompt.userChoice;
        } catch (err) {
          console.warn('Install prompt interaction failed', err);
        }
        deferredPrompt = null;
        if (banner) banner.hidden = true;
      });
    }

    const dismissBtns = $$('[data-dismiss-pwa], .pwa-banner .btn-ghost');
    dismissBtns.forEach((btn) => {
      btn.addEventListener('click', () => {
        localStorage.setItem('pwa-dismissed', '1');
        if (banner) banner.hidden = true;
      });
    });
  }

  function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) return;
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch((err) => {
        console.warn('Service worker registration failed', err);
      });
    });
  }

  function setDatetimeDefaults() {
    const inputs = $$('input[type="datetime-local"][data-default-now], #qc-reminder-at, #qc-event-start');
    if (!inputs.length) return;

    const now = new Date();
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

    inputs.forEach((input) => {
      if (!input.value) input.value = local;
    });
  }

  function autoResizeTextareas() {
    const resize = (el) => {
      el.style.height = 'auto';
      el.style.height = `${Math.min(el.scrollHeight, 420)}px`;
    };

    $$('textarea').forEach((ta) => {
      resize(ta);
      ta.addEventListener('input', () => resize(ta));
    });
  }

  function initKeyboardShortcuts() {
    doc.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        QuickCapture.close();
        return;
      }

      const isTyping = /input|textarea|select/i.test(doc.activeElement?.tagName || '');
      if (isTyping) return;

      if (e.key.toLowerCase() === 'n') {
        e.preventDefault();
        QuickCapture.open('task');
      }
    });
  }

  function initMobileNativeGuards() {
    doc.addEventListener('gesturestart', (e) => e.preventDefault(), { passive: false });
    doc.addEventListener('gesturechange', (e) => e.preventDefault(), { passive: false });
  }

  function wireGlobalFunctions() {
    window.openQuickCapture = (type) => QuickCapture.open(type || QuickCapture.getPreferredType());
    window.closeQuickCapture = () => QuickCapture.close();
  window.closeEntityFormModal = () => EntityFormModal.close();
    window.switchTab = (type) => QuickCapture.switchTab(type || 'task');
    window.dismissPWABanner = () => {
      localStorage.setItem('pwa-dismissed', '1');
      const banner = $('#install-banner, #pwa-banner');
      if (banner) banner.hidden = true;
    };

    if (typeof window.timerToggle !== 'function') window.timerToggle = () => {};
    if (typeof window.timerReset !== 'function') window.timerReset = () => {};
  }

  function init() {
    ThemeManager.init();
    QuickCapture.init();
    EntityFormModal.init();
    wireGlobalFunctions();
    initFlashMessages();
    initHTMXHandlers();
    initPWABanner();
    registerServiceWorker();
    setDatetimeDefaults();
    autoResizeTextareas();
    initKeyboardShortcuts();
    initMobileNativeGuards();

    updateClock();
    updateGreeting();
    window.setInterval(updateClock, 1000);
    window.setInterval(updateGreeting, 60 * 1000);

    /* Re-enable CSS transitions after first paint to prevent initial white flash */
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        document.documentElement.classList.remove('no-transition');
      });
    });
  }

  doc.addEventListener('DOMContentLoaded', init);

  window.ThemeManager = ThemeManager;
  window.updateClock = updateClock;
  window.updateGreeting = updateGreeting;
})();

// Extra UX helpers kept outside IIFE scope intentionally for template interoperability.
(function () {
  const doc = document;

  function enhanceTabButtons() {
    const tabs = Array.from(doc.querySelectorAll('.view-tab, .filter, .tag-filter'));
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        const group = tab.parentElement;
        if (!group) return;
        Array.from(group.children).forEach((sibling) => {
          if (sibling.classList) sibling.classList.remove('active');
          if (sibling.setAttribute) sibling.setAttribute('aria-pressed', 'false');
        });
        tab.classList.add('active');
        tab.setAttribute('aria-pressed', 'true');
      });
    });
  }

  function initRelativeDateHints() {
    const items = Array.from(doc.querySelectorAll('[data-datetime]'));
    if (!items.length) return;

    const fmt = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });

    const render = () => {
      const now = Date.now();
      items.forEach((el) => {
        const v = el.getAttribute('data-datetime');
        if (!v) return;
        const then = new Date(v).getTime();
        if (Number.isNaN(then)) return;
        const diffMin = Math.round((then - now) / 60000);
        let text;
        if (Math.abs(diffMin) < 60) text = fmt.format(diffMin, 'minute');
        else if (Math.abs(diffMin) < 60 * 24) text = fmt.format(Math.round(diffMin / 60), 'hour');
        else text = fmt.format(Math.round(diffMin / (60 * 24)), 'day');
        el.textContent = text;
      });
    };

    render();
    window.setInterval(render, 60000);
  }

  function installHtmxSwapAnimation() {
    doc.addEventListener('htmx:afterSwap', (e) => {
      const target = e.detail?.target;
      if (!target || !target.classList) return;
      target.classList.add('fade-in-target');
      window.setTimeout(() => target.classList.remove('fade-in-target'), 260);
    });
  }

  function init() {
    enhanceTabButtons();
    initRelativeDateHints();
    installHtmxSwapAnimation();
  }

  doc.addEventListener('DOMContentLoaded', init);
})();
