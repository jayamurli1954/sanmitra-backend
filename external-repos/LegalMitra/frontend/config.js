// Frontend Configuration
// This file allows you to easily change the API URL if the backend port changes

const isLocalHost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
const runtimeApiBaseUrl = (window.__SANMITRA_API_BASE_URL__ || localStorage.getItem('SANMITRA_API_BASE_URL') || '').trim();
const defaultProdApiBaseUrl = 'https://sanmitra-backend-staging-sg.onrender.com/api/v1';

const CONFIG = {
    // Backend API base URL
    // Local: fixed localhost API
    // Production: runtime override > localStorage override > default Render URL
    API_BASE_URL: isLocalHost
        ? 'http://127.0.0.1:8000/api/v1'
        : (runtimeApiBaseUrl || defaultProdApiBaseUrl),

    APP_KEY: 'legalmitra',

    // API endpoints
    ENDPOINTS: {
        LEGAL_RESEARCH: '/legal-research',
        DRAFT_DOCUMENT: '/draft-document',
        SEARCH_CASES: '/search-cases',
        SEARCH_STATUTE: '/search-statute',
        TEMPLATES: '/templates',
        COST_TRACKING: '/cost-tracking'
    }
};

function shouldInjectAppKey(input) {
    const rawUrl = typeof input === 'string' ? input : (input && input.url) || '';
    if (!rawUrl) {
        return false;
    }

    try {
        const apiBaseUrl = new URL(CONFIG.API_BASE_URL, window.location.origin).toString();
        const absoluteUrl = new URL(rawUrl, window.location.origin).toString();
        return absoluteUrl.startsWith(apiBaseUrl);
    } catch (_error) {
        return false;
    }
}

function patchFetchWithAppKey() {
    if (typeof window === 'undefined' || typeof window.fetch !== 'function' || window.__LEGALMITRA_FETCH_PATCHED__) {
        return;
    }

    const nativeFetch = window.fetch.bind(window);

    window.fetch = (input, init = {}) => {
        if (!shouldInjectAppKey(input)) {
            return nativeFetch(input, init);
        }

        const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
        if (CONFIG.APP_KEY && !headers.has('X-App-Key')) {
            headers.set('X-App-Key', CONFIG.APP_KEY);
        }

        return nativeFetch(input, { ...init, headers });
    };

    window.__LEGALMITRA_FETCH_PATCHED__ = true;
}

// Make it available globally
if (typeof window !== 'undefined') {
    window.CONFIG = CONFIG;
    patchFetchWithAppKey();
}

