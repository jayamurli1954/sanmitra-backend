/**
 * LegalMitra Service Worker
 * Enables offline functionality and faster loading
 */

const CACHE_NAME = 'legalmitra-v1.3.4'; // Cache refresh for diary toggle + template wizard routing fixes
const API_CACHE = 'legalmitra-api-v1';

// Files to cache for offline use
// Note: diary.js is NOT cached to ensure fresh updates
const STATIC_ASSETS = [
    '/',
    '/index.html',
    '/templates.html',
    '/template-marketplace.html',
    '/template-wizard.html',
    '/diary.html',
    '/limitation-calculator.html',
    '/compliance-checker.html',
    '/document-checklist.html',
    '/config.js',
    '/manifest.json',
    '/icons/icon-192x192.png',
    '/icons/icon-512x512.png'
];

// API endpoints that can be cached
const CACHEABLE_API_PATTERNS = [
    '/api/v1/templates/categories',
    '/api/v1/templates/summary',
    '/api/v1/models/recommended'
];

// Install event - cache static assets
self.addEventListener('install', (event) => {
    console.log('[Service Worker] Installing...');

    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('[Service Worker] Caching static assets');
            return cache.addAll(STATIC_ASSETS.map(url => {
                // Handle different base paths
                return new Request(url, { cache: 'reload' });
            })).catch(err => {
                console.warn('[Service Worker] Failed to cache some assets:', err);
            });
        })
    );

    // Force the waiting service worker to become the active service worker
    self.skipWaiting();
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
    console.log('[Service Worker] Activating...');

    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    // Delete ALL old caches to force complete refresh
                    if (cacheName !== CACHE_NAME) {
                        console.log('[Service Worker] Deleting old cache:', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        }).then(() => {
            // Force refresh of all clients immediately
            return self.clients.claim();
        }).then(() => {
            // Notify all clients to reload
            return self.clients.matchAll().then(clients => {
                clients.forEach(client => {
                    client.postMessage({ type: 'SW_UPDATED', action: 'reload' });
                });
            });
        })
    );
});

// Fetch event - serve from cache when offline
self.addEventListener('fetch', (event) => {
    const { request } = event;
    const url = new URL(request.url);

    // Skip cross-origin requests
    if (url.origin !== location.origin) {
        return;
    }

    // Handle API requests differently
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(handleApiRequest(request));
    } else if (url.pathname.includes('diary.js')) {
        // Always fetch diary.js fresh (no cache) to ensure updates are immediate
        event.respondWith(fetch(request));
    } else {
        // Static assets - cache first, falling back to network
        event.respondWith(handleStaticRequest(request));
    }
});

/**
 * Handle static asset requests
 * Strategy: Cache first, fall back to network
 */
async function handleStaticRequest(request) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request);

    if (cached) {
        console.log('[Service Worker] Serving from cache:', request.url);
        return cached;
    }

    try {
        const response = await fetch(request);

        // Cache successful responses
        if (response.status === 200) {
            cache.put(request, response.clone());
        }

        return response;
    } catch (error) {
        console.error('[Service Worker] Fetch failed:', error);

        // Return offline page if available
        const offlinePage = await cache.match('/index.html');
        if (offlinePage) {
            return offlinePage;
        }

        // Return a basic offline response
        return new Response('Offline - Please check your internet connection', {
            status: 503,
            statusText: 'Service Unavailable',
            headers: new Headers({
                'Content-Type': 'text/plain'
            })
        });
    }
}

/**
 * Handle API requests
 * Strategy: Network first, fall back to cache (for read-only endpoints)
 */
async function handleApiRequest(request) {
    const url = new URL(request.url);
    const isCacheable = CACHEABLE_API_PATTERNS.some(pattern =>
        url.pathname.includes(pattern)
    );

    // Only cache GET requests
    if (request.method !== 'GET') {
        return fetch(request);
    }

    try {
        const response = await fetch(request);

        // Cache successful GET responses for cacheable endpoints
        if (isCacheable && response.status === 200) {
            const cache = await caches.open(API_CACHE);
            cache.put(request, response.clone());
        }

        return response;
    } catch (error) {
        console.warn('[Service Worker] API fetch failed, trying cache:', error);

        // Fall back to cache for cacheable endpoints
        if (isCacheable) {
            const cache = await caches.open(API_CACHE);
            const cached = await cache.match(request);

            if (cached) {
                console.log('[Service Worker] Serving API from cache:', request.url);
                return cached;
            }
        }

        // Return error response
        return new Response(JSON.stringify({
            error: 'Network error - offline mode',
            message: 'Cannot connect to backend. Some features may be unavailable.'
        }), {
            status: 503,
            headers: new Headers({
                'Content-Type': 'application/json'
            })
        });
    }
}

// Handle background sync (for future use)
self.addEventListener('sync', (event) => {
    console.log('[Service Worker] Background sync:', event.tag);

    if (event.tag === 'sync-usage-data') {
        event.waitUntil(syncUsageData());
    }
});

async function syncUsageData() {
    // Sync any pending usage data when back online
    console.log('[Service Worker] Syncing usage data...');
    // Implementation for syncing pending data
}

// Handle push notifications (for future use)
self.addEventListener('push', (event) => {
    console.log('[Service Worker] Push notification received');

    const options = {
        body: event.data ? event.data.text() : 'New update from LegalMitra',
        icon: '/icons/icon-192x192.png',
        badge: '/icons/icon-72x72.png',
        vibrate: [200, 100, 200],
        tag: 'legalmitra-notification',
        requireInteraction: false
    };

    event.waitUntil(
        self.registration.showNotification('LegalMitra', options)
    );
});

// Handle notification clicks
self.addEventListener('notificationclick', (event) => {
    console.log('[Service Worker] Notification clicked');

    event.notification.close();

    event.waitUntil(
        clients.openWindow('/')
    );
});

console.log('[Service Worker] Loaded successfully');



