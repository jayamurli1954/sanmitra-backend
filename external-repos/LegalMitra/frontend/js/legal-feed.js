(function (window) {
    'use strict';

    const REFRESH_INTERVAL_MS = 30 * 60 * 1000;
    let casesNewsRefreshInterval = null;

    function apiBaseUrl() {
        try {
            if (typeof API_BASE_URL !== 'undefined' && API_BASE_URL) {
                return API_BASE_URL;
            }
        } catch (_) {
            // ignore
        }
        return window.API_BASE_URL || '/api/v1';
    }

    function esc(value) {
        if (typeof window.escapeHtml === 'function') {
            return window.escapeHtml(value);
        }
        if (value === null || value === undefined) {
            return '';
        }
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function formatContent(value) {
        if (typeof window.formatArticleContent === 'function') {
            return window.formatArticleContent(value);
        }
        return `<p>${esc(value || '')}</p>`;
    }

    async function loadMajorCases(forceWeb = false) {
        const container = document.getElementById('major-cases-container');
        if (!container) {
            return;
        }

        if (forceWeb || !container.children.length) {
            container.innerHTML = '<div class="loading-box"><div class="spinner" style="width: 30px; height: 30px; border-width: 2px; margin: 0 auto 10px;"></div>Loading recent judgments...</div>';
        }

        try {
            const base = apiBaseUrl();
            const url = forceWeb ? `${base}/major-cases?force_web=true` : `${base}/major-cases`;
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error('Failed to fetch cases');
            }

            const data = await response.json();
            const items = Array.isArray(data.cases) ? data.cases : [];

            if (items.length > 0) {
                container.innerHTML = '<ul>' + items.map((item) => {
                    const title = esc(item.title || 'Recent Judgment');
                    const court = esc(item.court || 'Supreme Court of India');
                    const year = esc(item.year || '');
                    const summary = esc(item.summary || '');
                    const query = esc(item.query || item.title || '');
                    const sourceUrl = esc(item.url || '');
                    return `
                        <li onclick="openArticleModal(this)"
                            data-title="${title}"
                            data-court="${court}"
                            data-year="${year}"
                            data-summary="${summary}"
                            data-query="${query}"
                            data-url="${sourceUrl}"
                            data-type="case">
                            <div class="case-item-title">${title}</div>
                            <div class="case-item-court">${court}</div>
                            <div class="case-item-summary">${summary}</div>
                        </li>
                    `;
                }).join('') + '</ul>';
            } else {
                container.innerHTML = '<div class="loading-box">No recent judgments found.</div>';
            }
        } catch (error) {
            console.error('Error loading major cases:', error);
            container.innerHTML = `
                <ul>
                    <li>
                        <div class="case-item-title">System Offline / No Data</div>
                        <div class="case-item-court">System Message</div>
                        <div class="case-item-summary">Error: ${esc(error && error.message ? error.message : 'Unknown error')}<br>Please check your connection and try again.</div>
                    </li>
                </ul>`;
        }
    }

    async function loadLegalNews(forceWeb = false) {
        const container = document.getElementById('legal-news-container');
        if (!container) {
            return;
        }

        if (forceWeb || !container.children.length) {
            container.innerHTML = '<div class="loading-box"><div class="spinner" style="width: 30px; height: 30px; border-width: 2px; margin: 0 auto 10px;"></div>Loading legal news...</div>';
        }

        try {
            const base = apiBaseUrl();
            const url = forceWeb ? `${base}/legal-news?force_web=true` : `${base}/legal-news`;
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error('Failed to fetch news');
            }

            const data = await response.json();
            const items = Array.isArray(data.news) ? data.news : [];

            if (items.length > 0) {
                container.innerHTML = '<ul>' + items.map((item) => {
                    const title = esc(item.title || 'Legal Update');
                    const source = esc(item.source || 'Legal News');
                    const date = esc(item.date || '');
                    const summary = esc(item.summary || '');
                    const query = esc(item.query || item.title || '');
                    const sourceUrl = esc(item.url || '');
                    return `
                        <li onclick="openArticleModal(this)"
                            data-title="${title}"
                            data-source="${source}"
                            data-date="${date}"
                            data-summary="${summary}"
                            data-query="${query}"
                            data-url="${sourceUrl}"
                            data-type="news">
                            <div class="news-item-title">${title}</div>
                            <div class="news-item-meta">${source}${date ? ' ? ' + date : ''}</div>
                            <div class="news-item-summary">${summary}</div>
                        </li>
                    `;
                }).join('') + '</ul>';
            } else {
                container.innerHTML = '<div class="loading-box">No legal news found.</div>';
            }
        } catch (error) {
            console.error('Error loading legal news:', error);
            container.innerHTML = '<div class="loading-box">Unable to load news at this time.</div>';
        }
    }

    async function openArticleModal(element) {
        const data = element && element.dataset ? element.dataset : {};
        const titleEl = document.getElementById('article-title');
        const sourceEl = document.getElementById('article-source');
        const dateEl = document.getElementById('article-date');
        const bodyEl = document.getElementById('article-body');
        const modalEl = document.getElementById('article-modal');
        const linkBtn = document.getElementById('article-link');

        if (!titleEl || !sourceEl || !dateEl || !bodyEl || !modalEl) {
            return;
        }

        titleEl.textContent = data.title || '';
        sourceEl.textContent = data.type === 'case' ? (data.court || 'Legal Update') : (data.source || 'Legal Update');
        dateEl.textContent = data.type === 'case' ? (data.year || '') : (data.date || '');

        bodyEl.innerHTML = `<p>${esc(data.summary || '')}</p><hr><p class="article-loading" style="text-align:center;color:#666;"><em>Analyzing details with AI...</em></p>`;

        if (linkBtn) {
            if (data.url && data.url !== 'undefined') {
                linkBtn.href = data.url;
                linkBtn.style.display = 'inline-block';
            } else {
                linkBtn.style.display = 'none';
            }
        }

        modalEl.style.display = 'block';

        if (!data.query) {
            return;
        }

        try {
            const base = apiBaseUrl();
            const response = await fetch(`${base}/legal-research`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query: data.query,
                    query_type: 'research'
                })
            });

            if (response.ok) {
                const resData = await response.json();
                if (resData.response) {
                    bodyEl.innerHTML = `<p>${formatContent(resData.response)}</p>`;
                    return;
                }
            }

            bodyEl.innerHTML = `<p>${esc(data.summary || '')}</p><hr><p style="color:#e67e22;text-align:center;"><em>(AI analysis unavailable, showing summary only)</em></p>`;
        } catch (error) {
            console.error(error);
            bodyEl.innerHTML = `<p>${esc(data.summary || '')}</p><hr><p style="color:#e67e22;text-align:center;"><em>(AI analysis request failed, showing summary only)</em></p>`;
        }
    }

    function closeArticleModal() {
        const modal = document.getElementById('article-modal');
        if (modal) {
            modal.style.display = 'none';
        }
    }

    async function refreshCasesAndNews() {
        const refreshButtons = document.querySelectorAll('.refresh-btn');
        const originalTexts = [];

        refreshButtons.forEach((btn, index) => {
            originalTexts[index] = btn.textContent;
            btn.disabled = true;
            btn.style.opacity = '0.6';
            btn.style.cursor = 'not-allowed';
            btn.textContent = 'Refreshing...';
        });

        try {
            const casesContainer = document.getElementById('major-cases-container');
            const newsContainer = document.getElementById('legal-news-container');

            if (casesContainer) {
                casesContainer.innerHTML = '<div class="loading-box"><div class="spinner" style="width: 30px; height: 30px; border-width: 2px; margin: 0 auto 10px;"></div>Refreshing cases...</div>';
            }
            if (newsContainer) {
                newsContainer.innerHTML = '<div class="loading-box"><div class="spinner" style="width: 30px; height: 30px; border-width: 2px; margin: 0 auto 10px;"></div>Refreshing news...</div>';
            }

            await Promise.all([
                loadMajorCases(true),
                loadLegalNews(true)
            ]);
        } catch (error) {
            console.error('Error refreshing cases and news:', error);
            alert('Error refreshing content. Please check the console for details.');
        } finally {
            setTimeout(() => {
                refreshButtons.forEach((btn, index) => {
                    btn.disabled = false;
                    btn.style.opacity = '1';
                    btn.style.cursor = 'pointer';
                    btn.textContent = originalTexts[index] || 'Refresh';
                });
            }, 500);
        }
    }

    function initCasesAndNews() {
        setTimeout(() => {
            loadMajorCases();
            loadLegalNews();
        }, 200);

        if (casesNewsRefreshInterval) {
            clearInterval(casesNewsRefreshInterval);
        }

        casesNewsRefreshInterval = setInterval(() => {
            refreshCasesAndNews();
        }, REFRESH_INTERVAL_MS);
    }

    window.loadMajorCases = loadMajorCases;
    window.loadLegalNews = loadLegalNews;
    window.openArticleModal = openArticleModal;
    window.closeArticleModal = closeArticleModal;
    window.refreshCasesAndNews = refreshCasesAndNews;
    window.initCasesAndNews = initCasesAndNews;
})(window);
