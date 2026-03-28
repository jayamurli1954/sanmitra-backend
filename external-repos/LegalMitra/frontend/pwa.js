/**
 * PWA Helper Script for LegalMitra
 * Handles service worker registration and install prompts
 */

let deferredPrompt;
let installButton;

// Register service worker
if ('serviceWorker' in navigator) {
    window.addEventListener('load', async () => {
        try {
            const registration = await navigator.serviceWorker.register('/service-worker.js', {
                scope: '/'
            });

            console.log('✅ Service Worker registered:', registration.scope);

            // Check for updates periodically
            setInterval(() => {
                registration.update();
            }, 60 * 60 * 1000); // Check every hour

            // Listen for updates
            registration.addEventListener('updatefound', () => {
                const newWorker = registration.installing;

                newWorker.addEventListener('statechange', () => {
                    if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                        showUpdateNotification();
                    }
                });
            });

        } catch (error) {
            console.error('❌ Service Worker registration failed:', error);
        }
    });
}

// Capture the install prompt event
window.addEventListener('beforeinstallprompt', (e) => {
    console.log('💾 Install prompt available');

    // Prevent the mini-infobar from appearing on mobile
    e.preventDefault();

    // Store the event for later use
    deferredPrompt = e;

    // Show install button
    showInstallButton();
});

// Handle successful installation
window.addEventListener('appinstalled', () => {
    console.log('✅ PWA installed successfully');

    // Hide install button
    hideInstallButton();

    // Clear the deferredPrompt
    deferredPrompt = null;

    // Show thank you message
    showNotification('LegalMitra installed! You can now access it from your home screen.', 'success');
});

/**
 * Show install button in UI
 */
function showInstallButton() {
    // Check if button already exists
    if (document.getElementById('pwa-install-button')) {
        return;
    }

    // Create install button
    const installBtn = document.createElement('button');
    installBtn.id = 'pwa-install-button';
    installBtn.innerHTML = '📱 Install App';
    installBtn.style.cssText = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        padding: 15px 25px;
        border-radius: 30px;
        font-size: 16px;
        font-weight: bold;
        cursor: pointer;
        box-shadow: 0 5px 20px rgba(102, 126, 234, 0.4);
        z-index: 10000;
        transition: all 0.3s ease;
    `;

    installBtn.addEventListener('mouseover', () => {
        installBtn.style.transform = 'translateY(-3px)';
        installBtn.style.boxShadow = '0 8px 25px rgba(102, 126, 234, 0.5)';
    });

    installBtn.addEventListener('mouseout', () => {
        installBtn.style.transform = 'translateY(0)';
        installBtn.style.boxShadow = '0 5px 20px rgba(102, 126, 234, 0.4)';
    });

    installBtn.addEventListener('click', installApp);

    document.body.appendChild(installBtn);
    installButton = installBtn;

}

/**
 * Hide install button
 */
function hideInstallButton() {
    if (installButton && installButton.parentNode) {
        installButton.style.opacity = '0';
        setTimeout(() => {
            if (installButton && installButton.parentNode) {
                installButton.remove();
            }
        }, 300);
    }
}

/**
 * Trigger install prompt
 */
async function installApp() {
    if (!deferredPrompt) {
        console.log('Install prompt not available');
        return;
    }

    // Show the install prompt
    deferredPrompt.prompt();

    // Wait for the user's response
    const { outcome } = await deferredPrompt.userChoice;

    console.log(`User response: ${outcome}`);

    if (outcome === 'accepted') {
        console.log('User accepted the install prompt');
    } else {
        console.log('User dismissed the install prompt');
    }

    // Clear the deferredPrompt
    deferredPrompt = null;

    // Hide install button
    hideInstallButton();
}

/**
 * Show update notification
 */
function showUpdateNotification() {
    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        left: 50%;
        transform: translateX(-50%);
        background: #667eea;
        color: white;
        padding: 15px 25px;
        border-radius: 10px;
        box-shadow: 0 5px 20px rgba(0,0,0,0.3);
        z-index: 10001;
        display: flex;
        align-items: center;
        gap: 15px;
    `;

    notification.innerHTML = `
        <span>🔄 New version available!</span>
        <button onclick="window.location.reload()" style="
            background: white;
            color: #667eea;
            border: none;
            padding: 8px 16px;
            border-radius: 5px;
            cursor: pointer;
            font-weight: bold;
        ">Update Now</button>
    `;

    document.body.appendChild(notification);

    // Auto-remove after 10 seconds
    setTimeout(() => {
        notification.style.opacity = '0';
        setTimeout(() => notification.remove(), 300);
    }, 10000);
}

/**
 * Show notification helper
 */
function showNotification(message, type = 'info') {
    const colors = {
        success: '#56ab2f',
        error: '#d32f2f',
        info: '#667eea',
        warning: '#ff9800'
    };

    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        background: ${colors[type]};
        color: white;
        padding: 15px 25px;
        border-radius: 10px;
        box-shadow: 0 5px 20px rgba(0,0,0,0.3);
        z-index: 10001;
        max-width: 300px;
        animation: slideIn 0.3s ease;
    `;

    notification.textContent = message;

    document.body.appendChild(notification);

    setTimeout(() => {
        notification.style.opacity = '0';
        setTimeout(() => notification.remove(), 300);
    }, 5000);
}

/**
 * Check if running as installed PWA
 */
function isInstalledPWA() {
    return window.matchMedia('(display-mode: standalone)').matches ||
           window.navigator.standalone === true;
}

/**
 * Add iOS-specific meta tags dynamically
 */
function addIOSMetaTags() {
    const meta = [
        { name: 'apple-mobile-web-app-capable', content: 'yes' },
        { name: 'apple-mobile-web-app-status-bar-style', content: 'black-translucent' },
        { name: 'apple-mobile-web-app-title', content: 'LegalMitra' }
    ];

    meta.forEach(({ name, content }) => {
        const tag = document.createElement('meta');
        tag.name = name;
        tag.content = content;
        document.head.appendChild(tag);
    });

    // Add apple-touch-icon
    const link = document.createElement('link');
    link.rel = 'apple-touch-icon';
    link.href = '/icons/icon-192x192.png';
    document.head.appendChild(link);
}

// Initialize
if (isInstalledPWA()) {
    console.log('✅ Running as installed PWA');
    document.body.classList.add('pwa-mode');
} else {
    console.log('📱 Running in browser mode');
}

// Add iOS meta tags
addIOSMetaTags();

// Log PWA status
console.log('PWA Helper initialized');

// Export functions for use in other scripts
window.PWA = {
    install: installApp,
    isInstalled: isInstalledPWA,
    showNotification: showNotification
};
