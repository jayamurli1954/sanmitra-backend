        // Use configuration from config.js (defaults to port 8888)
        const API_BASE_URL = (typeof CONFIG !== 'undefined') ? CONFIG.API_BASE_URL : 'http://127.0.0.1:8000/api/v1';
        
        // Listen for service worker updates
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.addEventListener('message', (event) => {
                if (event.data && event.data.type === 'SW_UPDATED') {
                    console.log('Service worker updated, reloading page...');
                    window.location.reload();
                }
            });
        }

        function switchTab(tabName) {
            // Hide all tab contents
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.remove('active');
            });
            document.querySelectorAll('.tab').forEach(tab => {
                tab.classList.remove('active');
            });

            // Show selected tab
            document.getElementById(tabName).classList.add('active');
            if (event && event.target) {
                event.target.classList.add('active');
            } else {
                // Fallback: activate tab by index
                const tabs = document.querySelectorAll('.tab');
                const tabMap = { 'research': 0, 'drafting': 1, 'case-search': 2, 'statute': 3 };
                if (tabMap[tabName] !== undefined) {
                    tabs[tabMap[tabName]]?.classList.add('active');
                }
            }

            // Reload recent queries if switching to research tab
            if (tabName === 'research') {
                initRecentQueries();
            }
        }

        function openModal(modalName) {
            const modal = document.getElementById(`${modalName}-modal`);
            if (modal) {
                modal.style.display = 'block';
                document.body.style.overflow = 'hidden'; // Prevent background scrolling
            }
        }

        function closeModal(modalName) {
            const modal = document.getElementById(`${modalName}-modal`);
            if (modal) {
                modal.style.display = 'none';
                document.body.style.overflow = 'auto'; // Restore scrolling
            }
        }

        // Close modal when clicking outside of it
        window.onclick = function (event) {
            if (event.target.classList.contains('modal')) {
                event.target.style.display = 'none';
                document.body.style.overflow = 'auto';
            }
        }

        // Close modal with Escape key
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                document.querySelectorAll('.modal').forEach(modal => {
                    if (modal.style.display === 'block') {
                        modal.style.display = 'none';
                        document.body.style.overflow = 'auto';
                    }
                });
            }
        });

        function showLoading(elementId) {
            document.getElementById(elementId).innerHTML = `
                <div class="response loading">
                    <div class="spinner"></div>
                    <p>Processing your request...</p>
                </div>
            `;
        }

        function showResponse(elementId, content, isError = false, citations = [], note = "") {
            console.log('showResponse called:', { elementId, contentLength: content ? content.length : 0, isError, citations: Array.isArray(citations) ? citations.length : 0, note: note || '' });

            const element = document.getElementById(elementId);
            if (!element) {
                console.error('ERROR: Response element not found:', elementId);
                alert('Error: Response container not found: ' + elementId);
                return;
            }

            console.log('Element found:', element);

            const className = isError ? 'response error' : 'response success';
            // Escape HTML but preserve line breaks
            const escapedContent = String(content || '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;')
                .replace(/\n/g, '<br>');
            const sourcesHtml = !isError && Array.isArray(citations) && citations.length > 0
                ? renderSourcesPanel(citations)
                : '';
            const noteHtml = !isError && note
                ? `<div class="response-note">${escapeHtml(String(note))}</div>`
                : '';

            const html = `
                <div class="${className}">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                        <h3 style="margin: 0;">${isError ? '⚠️ Error' : '✅ Response'}</h3>
                        ${!isError ? `<button onclick="downloadResponse('${elementId}')" style="background: #667eea; color: white; border: none; padding: 8px 16px; border-radius: 5px; cursor: pointer; font-size: 14px;" title="Download Response">📥 Download</button>` : ''}
                    </div>
                    <div class="response-content" data-original-content="${escapeHtml(content).replace(/"/g, '&quot;')}">${escapedContent}</div>
                    ${sourcesHtml}
                    ${noteHtml}
                </div>
            `;

            console.log('Setting innerHTML, length:', html.length);
            element.innerHTML = html;
            element.style.display = 'block'; // Ensure it's visible
            console.log('Response displayed in element:', element);

            // Scroll to response after a brief delay
            setTimeout(() => {
                element.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }, 100);
        }

        // Recent Queries Functions
        function saveRecentQuery(query) {
            if (!query || !query.trim()) return;

            let recentQueries = JSON.parse(localStorage.getItem('legalmitra_recent_queries') || '[]');

            // Remove if already exists (to avoid duplicates)
            recentQueries = recentQueries.filter(q => q.text !== query.trim());

            // Add to beginning
            recentQueries.unshift({
                text: query.trim(),
                timestamp: new Date().toISOString()
            });

            // Keep only last 20 queries
            recentQueries = recentQueries.slice(0, 20);

            localStorage.setItem('legalmitra_recent_queries', JSON.stringify(recentQueries));
            loadRecentQueries(); // Refresh the list
        }

        function loadRecentQueries() {
            try {
                const recentQueries = JSON.parse(localStorage.getItem('legalmitra_recent_queries') || '[]');
                const listContainer = document.getElementById('recent-queries-list');

                if (!listContainer) return; // Element might not exist yet

                // Filter out invalid entries and clean up data
                const validQueries = recentQueries.filter(q => q && (q.text || q.query)).map(q => ({
                    text: q.text || q.query || '',
                    timestamp: q.timestamp || new Date().toISOString()
                })).filter(q => q.text.trim().length > 0);

                // Update localStorage with cleaned data
                if (validQueries.length !== recentQueries.length) {
                    localStorage.setItem('legalmitra_recent_queries', JSON.stringify(validQueries));
                }

                if (validQueries.length === 0) {
                    listContainer.innerHTML = '<p style="color: #999; text-align: center; padding: 20px;">No recent queries yet. Your queries will appear here.</p>';
                    return;
                }

                listContainer.innerHTML = validQueries.map((query, index) => {
                    const queryText = query.text || '';
                    const displayText = queryText.length > 100 ? queryText.substring(0, 100) + '...' : queryText;
                    const timestamp = query.timestamp ? formatTimestamp(new Date(query.timestamp)) : 'Unknown time';

                    return `
                        <div class="recent-query-item" style="padding: 12px; margin-bottom: 8px; background: white; border-radius: 6px; border-left: 3px solid #667eea; cursor: pointer; transition: all 0.2s;" 
                             onmouseover="this.style.background='#f0f0f0'; this.style.transform='translateX(3px)'" 
                             onmouseout="this.style.background='white'; this.style.transform='translateX(0)'"
                             onclick="useRecentQuery(${index})">
                            <div style="font-weight: 500; color: #333; margin-bottom: 4px;">${escapeHtml(displayText)}</div>
                            <div style="font-size: 0.85em; color: #999;">${timestamp}</div>
                        </div>
                    `;
                }).join('');
            } catch (error) {
                console.error('Error loading recent queries:', error);
                // Clear invalid localStorage data
                try {
                    localStorage.removeItem('legalmitra_recent_queries');
                } catch (e) {
                    console.error('Error clearing localStorage:', e);
                }
            }
        }

        function useRecentQuery(index) {
            try {
                const recentQueries = JSON.parse(localStorage.getItem('legalmitra_recent_queries') || '[]');
                const validQueries = recentQueries.filter(q => q && (q.text || q.query));

                if (index >= 0 && index < validQueries.length) {
                    const queryText = validQueries[index].text || validQueries[index].query || '';
                    const queryInput = document.getElementById('research-query');
                    if (queryInput) {
                        queryInput.value = queryText;
                        queryInput.focus();
                        queryInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                }
            } catch (error) {
                console.error('Error using recent query:', error);
            }
        }

        function toggleRecentQueries() {
            const listContainer = document.getElementById('recent-queries-list');
            const toggleIcon = document.getElementById('toggle-icon');

            if (listContainer && toggleIcon) {
                if (listContainer.style.display === 'none' || !listContainer.style.display) {
                    listContainer.style.display = 'block';
                    toggleIcon.textContent = '▲';
                } else {
                    listContainer.style.display = 'none';
                    toggleIcon.textContent = '▼';
                }
            }
        }

        function formatTimestamp(date) {
            const now = new Date();
            const diff = now - date;
            const seconds = Math.floor(diff / 1000);
            const minutes = Math.floor(seconds / 60);
            const hours = Math.floor(minutes / 60);
            const days = Math.floor(hours / 24);

            if (seconds < 60) return 'Just now';
            if (minutes < 60) return `${minutes} minute${minutes > 1 ? 's' : ''} ago`;
            if (hours < 24) return `${hours} hour${hours > 1 ? 's' : ''} ago`;
            if (days < 7) return `${days} day${days > 1 ? 's' : ''} ago`;

            // Format as date if older than a week
            return date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        }


        function isHttpUrl(value) {
            if (!value || typeof value !== 'string') return false;
            try {
                const parsed = new URL(value);
                return parsed.protocol === 'http:' || parsed.protocol === 'https:';
            } catch (e) {
                return false;
            }
        }

        function renderSourcesPanel(citations) {
            const ordered = [...citations].sort((a, b) => {
                const ai = Number(a?.index ?? 9999);
                const bi = Number(b?.index ?? 9999);
                return ai - bi;
            });

            const cards = ordered.map(citation => {
                const legal = citation?.legal_metadata || {};
                const sourceType = citation?.source_type ? String(citation.source_type).toUpperCase() : '';
                const title = citation?.title || citation?.reference || 'Untitled Source';
                const sourceUri = citation?.source_uri || '';
                const indexLabel = citation?.index ? `[${citation.index}]` : '[?]';
                const referenceText = citation?.reference || '';

                const badges = [
                    sourceType,
                    legal?.court_name || '',
                    legal?.section ? `Section ${legal.section}` : '',
                    legal?.doc_date || ''
                ].filter(Boolean).map(tag => `<span class="source-badge">${escapeHtml(String(tag))}</span>`).join('');

                const uriBlock = isHttpUrl(sourceUri)
                    ? `<a class="source-uri" href="${escapeHtml(sourceUri)}" target="_blank" rel="noopener noreferrer">${escapeHtml(sourceUri)}</a>`
                    : '';

                return `
                    <div class="source-card">
                        <div class="source-card-header">
                            <span class="source-index">${escapeHtml(indexLabel)}</span>
                            <div class="source-title">${escapeHtml(title)}</div>
                        </div>
                        ${badges ? `<div class="source-badges">${badges}</div>` : ''}
                        ${referenceText ? `<div class="source-reference">${escapeHtml(referenceText)}</div>` : ''}
                        ${uriBlock}
                    </div>
                `;
            }).join('');

            return `
                <div class="sources-panel">
                    <h4>Sources (${ordered.length})</h4>
                    <div class="sources-grid">${cards}</div>
                </div>
            `;
        }





        // Helper function for HTML escaping - Moved to top scope
        function escapeHtml(text) {
            if (!text) return text;
            return String(text)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        function closeArticleModal() {
            const modal = document.getElementById('article-modal');
            if (modal) {
                modal.classList.remove('show');
            }
        }

        function formatArticleContent(content) {
            // Convert plain text to formatted HTML
            // Preserve line breaks and structure
            let html = escapeHtml(content);

            // Convert double line breaks to paragraphs
            html = html.replace(/\n\n+/g, '</p><p>');
            html = '<p>' + html + '</p>';

            // Convert single line breaks to <br>
            html = html.replace(/\n/g, '<br>');

            // Format headings (lines that are all caps or end with :)
            html = html.replace(/<p>(<strong>)?([A-Z][^:]+):(<\/strong>)?<\/p>/g, '<h3>$2</h3>');

            // Format bold text patterns
            html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

            return html;
        }


        // Load recent queries when page loads and when tab is switched
        function initRecentQueries() {
            // Wait a bit to ensure DOM is ready
            setTimeout(() => {
                loadRecentQueries();
            }, 100);
        }

        // Load template count dynamically (for marketplace - uses v2 API)
        async function loadTemplateCount() {
            try {
                const url = `${API_BASE_URL}/v2/templates`;
                const response = await fetch(url);
                
                if (!response.ok) {
                    console.error('Failed to fetch template count:', response.status, response.statusText);
                    return; // Silently fail, keep default button text
                }
                
                // Check if response is JSON
                const contentType = response.headers.get('content-type');
                if (!contentType || !contentType.includes('application/json')) {
                    console.warn('Template count response is not JSON:', contentType);
                    return;
                }
                
                const data = await response.json();
                const count = data.total || 0;
                const button = document.getElementById('template-marketplace-btn');
                if (button) {
                    // Update button text with count
                    button.innerHTML = `🛒 Template Marketplace (${count})`;
                }
            } catch (error) {
                console.log('Could not load template count:', error.message);
                // If API fails, silently keep default button text
            }
        }

        window.addEventListener('DOMContentLoaded', function () {
            initRecentQueries();
            initCasesAndNews();
            loadTemplateCount();

            // Close article modal when clicking outside
            const articleModal = document.getElementById('article-modal');
            if (articleModal) {
                articleModal.addEventListener('click', function (event) {
                    if (event.target === articleModal) {
                        closeArticleModal();
                    }
                });
            }

            // Close article modal with Escape key
            document.addEventListener('keydown', function (event) {
                if (event.key === 'Escape') {
                    closeArticleModal();
                }
            });
        });

        // File handling variables
        let selectedFile = null;

        // Handle File Select
        function handleFileSelect(event) {
            const file = event.target.files[0];
            if (!file) return;

            const allowedTypes = ['application/pdf', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'image/png', 'image/jpeg', 'image/jpg', 'text/plain'];
            if (!allowedTypes.includes(file.type)) {
                alert('Please select a PDF, DOC, DOCX, PNG, JPEG, or TXT file');
                event.target.value = '';
                return;
            }

            if (file.size > 10 * 1024 * 1024) { // 10MB limit
                alert('File size must be less than 10MB');
                event.target.value = '';
                return;
            }

            selectedFile = file;
            showFilePreview(file);
        }

        // Show File Preview
        function showFilePreview(file) {
            const container = document.getElementById('file-preview-container');
            if (!container) return;

            const fileExtension = file.name.split('.').pop().toUpperCase();
            const fileSize = formatFileSize(file.size);

            container.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <span style="font-size: 24px;">📄</span>
                        <div>
                            <div style="font-weight: bold;">${escapeHtml(file.name)}</div>
                            <div style="font-size: 0.9em; color: #666;">${fileExtension} • ${fileSize}</div>
                        </div>
                    </div>
                    <button onclick="removeFile()" style="background: #e74c3c; color: white; border: none; padding: 5px 10px; border-radius: 3px; cursor: pointer;">✕ Remove</button>
                </div>
            `;
            container.style.display = 'block';
        }

        // Remove File
        function removeFile() {
            selectedFile = null;
            const container = document.getElementById('file-preview-container');
            const fileInput = document.getElementById('research-file-input');
            if (container) container.style.display = 'none';
            if (fileInput) fileInput.value = '';
        }

        // Format File Size
        function formatFileSize(bytes) {
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        }

        // Download Response
        function downloadResponse(elementId) {
            try {
                const element = document.getElementById(elementId);
                if (!element) {
                    alert('Response not found');
                    return;
                }

                // Get the original content from data attribute or extract from DOM
                const contentDiv = element.querySelector('.response-content');
                let content = '';

                if (contentDiv && contentDiv.getAttribute('data-original-content')) {
                    // Decode HTML entities from data attribute
                    const textarea = document.createElement('textarea');
                    textarea.innerHTML = contentDiv.getAttribute('data-original-content');
                    content = textarea.value;
                } else {
                    // Fallback: extract text from DOM (will lose formatting)
                    content = contentDiv ? contentDiv.textContent || contentDiv.innerText : '';
                }

                if (!content) {
                    alert('No content to download');
                    return;
                }

                // Create a blob with the content
                const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
                const url = window.URL.createObjectURL(blob);

                // Create download link
                const a = document.createElement('a');
                a.href = url;
                a.download = `LegalMitra_Response_${new Date().toISOString().slice(0, 10)}_${Date.now()}.txt`;
                document.body.appendChild(a);
                a.click();

                // Cleanup
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
            } catch (error) {
                console.error('Error downloading response:', error);
                alert('Error downloading response. Please try copying the text manually.');
            }
        }

        async function performResearch() {
            const queryInput = document.getElementById('research-query');
            if (!queryInput) {
                alert('Error: Cannot find query input field. Please refresh the page.');
                return;
            }

            let query = queryInput.value;
            const queryType = document.getElementById('query-type').value;

            // If file is selected, use document review endpoint
            if (selectedFile) {
                await performDocumentReview(query);
                return;
            }

            if (!query || !query.trim()) {
                alert('Please enter a legal question');
                return;
            }

            const responseDiv = document.getElementById('research-response');
            if (!responseDiv) {
                alert('Error: Response container not found. Please refresh the page.');
                return;
            }

            // Save to recent queries
            saveRecentQuery(query);

            // Clear any previous response
            responseDiv.innerHTML = '';
            responseDiv.style.display = 'block'; // Ensure it's visible

            showLoading('research-response');

            try {
                const apiUrl = `${API_BASE_URL}/legal-research`;

                // Add timeout (180 seconds for AI responses - complex legal queries may take longer)
                const controller = new AbortController();
                const timeoutId = setTimeout(() => {
                    controller.abort();
                }, 180000);

                const requestBody = {
                    query: query,
                    query_type: queryType
                };

                const response = await fetch(apiUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(requestBody),
                    signal: controller.signal
                });

                clearTimeout(timeoutId);

                let data;
                let responseText = '';
                try {
                    responseText = await response.text();
                    // Check if response is empty
                    if (!responseText || responseText.trim().length === 0) {
                        throw new Error('Server returned empty response');
                    }
                    // Check if response looks like HTML (error page)
                    if (responseText.trim().startsWith('<!DOCTYPE') || responseText.trim().startsWith('<html')) {
                        throw new Error('Server returned HTML instead of JSON. The API endpoint may not exist.');
                    }
                    data = JSON.parse(responseText);
                } catch (parseError) {
                    console.error('JSON parse error:', parseError);
                    console.error('API URL:', apiUrl);
                    console.error('Response status:', response.status);
                    console.error('Response headers:', response.headers);
                    console.error('Response text (first 500 chars):', responseText.substring(0, 500));
                    const errorMsg = `Server returned invalid response from ${apiUrl}.\n\nStatus: ${response.status}\nResponse: ${responseText.substring(0, 200)}...\n\nPlease check the API endpoint and try again.`;
                    throw new Error(errorMsg);
                }

                if (response.ok) {
                    if (data && data.response) {
                        showResponse('research-response', data.response, false, data.citations || [], data.note || '');
                        // Clear selected file after successful response
                        removeFile();
                    } else {
                        const errorMsg = 'Received invalid response from server. Please try again.';
                        showResponse('research-response', errorMsg, true);
                    }
                } else {
                    const errorMsg = data?.detail || data?.error || data?.message || `Server error (${response.status})`;
                    showResponse('research-response', `Error ${response.status}: ${errorMsg}`, true);
                }
            } catch (error) {
                console.error('Request error:', error);

                if (error.name === 'AbortError') {
                    showResponse('research-response', 'Request timed out after 3 minutes. Complex AI responses can take time. Please try a simpler query or try again.', true);
                } else if (error.message.includes('fetch') || error.message.includes('Failed to fetch') || error.name === 'TypeError') {
                    const errorMsg = `Connection error: Cannot connect to server.\n\nPlease check:\n1. Backend server is running\n2. No firewall blocking the connection\n3. CORS is enabled\n\nError: ${error.message}`;
                    alert(errorMsg);
                    showResponse('research-response', errorMsg, true);
                } else {
                    const errorMsg = `Error: ${error.message}\n\nPlease check your connection and try again.`;
                    alert(errorMsg);
                    showResponse('research-response', errorMsg, true);
                }
            }

            console.log('=== performResearch() COMPLETED ===');
        }

        // Perform Document Review with File Upload
        async function performDocumentReview(query = '') {
            if (!selectedFile) {
                alert('No file selected');
                return;
            }

            const responseDiv = document.getElementById('research-response');
            if (!responseDiv) {
                alert('Error: Response container not found. Please refresh the page.');
                return;
            }

            // Save to recent queries
            if (query) {
                saveRecentQuery(query);
            }

            // Clear any previous response
            responseDiv.innerHTML = '';
            responseDiv.style.display = 'block';

            showLoading('research-response');

            try {
                const apiUrl = `${API_BASE_URL}/review-document`;

                // Add timeout (180 seconds for document processing)
                const controller = new AbortController();
                const timeoutId = setTimeout(() => {
                    controller.abort();
                }, 180000);

                // Create FormData for file upload
                const formData = new FormData();
                formData.append('file', selectedFile);
                if (query && query.trim()) {
                    formData.append('query', query);
                }

                const response = await fetch(apiUrl, {
                    method: 'POST',
                    body: formData,
                    signal: controller.signal
                });

                clearTimeout(timeoutId);

                let data;
                let responseText = '';
                try {
                    responseText = await response.text();
                    // Check if response is empty
                    if (!responseText || responseText.trim().length === 0) {
                        throw new Error('Server returned empty response');
                    }
                    // Check if response looks like HTML (error page)
                    if (responseText.trim().startsWith('<!DOCTYPE') || responseText.trim().startsWith('<html')) {
                        throw new Error('Server returned HTML instead of JSON. The API endpoint may not exist.');
                    }
                    data = JSON.parse(responseText);
                } catch (parseError) {
                    console.error('JSON parse error:', parseError);
                    console.error('API URL:', apiUrl);
                    console.error('Response status:', response.status);
                    console.error('Response headers:', response.headers);
                    console.error('Response text (first 500 chars):', responseText.substring(0, 500));
                    const errorMsg = `Server returned invalid response from ${apiUrl}.\n\nStatus: ${response.status}\nResponse: ${responseText.substring(0, 200)}...\n\nPlease check the API endpoint and try again.`;
                    throw new Error(errorMsg);
                }

                if (response.ok) {
                    if (data && data.analysis) {
                        showResponse('research-response', data.analysis);
                        // Clear selected file after successful response
                        removeFile();
                    } else {
                        const errorMsg = 'Received invalid response from server. Please try again.';
                        showResponse('research-response', errorMsg, true);
                    }
                } else {
                    const errorMsg = data?.detail || data?.error || data?.message || `Server error (${response.status})`;
                    showResponse('research-response', `Error ${response.status}: ${errorMsg}`, true);
                }
            } catch (error) {
                console.error('Document review error:', error);

                if (error.name === 'AbortError') {
                    showResponse('research-response', 'Request timed out after 3 minutes. Document processing can take time. Please try again.', true);
                } else if (error.message.includes('fetch') || error.message.includes('Failed to fetch') || error.name === 'TypeError') {
                    const errorMsg = `Connection error: Cannot connect to server.\n\nPlease check:\n1. Backend server is running\n2. No firewall blocking the connection\n3. CORS is enabled\n\nError: ${error.message}`;
                    alert(errorMsg);
                    showResponse('research-response', errorMsg, true);
                } else {
                    const errorMsg = `Error: ${error.message}\n\nPlease check your connection and try again.`;
                    alert(errorMsg);
                    showResponse('research-response', errorMsg, true);
                }
            }
        }

        async function draftDocument() {
            const docType = document.getElementById('doc-type').value;
            const facts = document.getElementById('facts').value;
            const partiesText = document.getElementById('parties').value;
            const groundsText = document.getElementById('grounds').value;
            const prayer = document.getElementById('prayer').value;
            const amountDue = document.getElementById('draft-amount-due')?.value || '';
            const invoiceRef = document.getElementById('draft-invoice-ref')?.value || '';
            const dueDate = document.getElementById('draft-due-date')?.value || '';
            const jurisdiction = document.getElementById('draft-jurisdiction')?.value || '';
            const interestRate = document.getElementById('draft-interest-rate')?.value || '';
            const noticePeriod = document.getElementById('draft-notice-period')?.value || '';

            if (!facts.trim() || !partiesText.trim() || !groundsText.trim() || !prayer.trim()) {
                alert('Please fill all required fields');
                return;
            }

            let parties;
            try {
                parties = JSON.parse(partiesText);
            } catch (e) {
                alert('Invalid JSON format for parties. Use format: {"sender": "ABC", "recipient": "XYZ"}');
                return;
            }

            const grounds = groundsText.split('\n').filter(g => g.trim());

            showLoading('drafting-response');

            try {
                // Add timeout (180 seconds for document drafting)
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 180000);

                const response = await fetch(`${API_BASE_URL}/draft-document`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        document_type: docType,
                        facts: facts,
                        parties: parties,
                        legal_grounds: grounds,
                        prayer: prayer,
                        amount_due: amountDue,
                        invoice_ref: invoiceRef,
                        due_date: dueDate,
                        jurisdiction: jurisdiction,
                        interest_rate: interestRate,
                        notice_period: noticePeriod
                    }),
                    signal: controller.signal
                });

                clearTimeout(timeoutId);
                const data = await response.json();

                if (response.ok) {
                    const draftText = data.drafted_document || 'Draft generated, but content is empty.';
                    const extras = [];

                    if (data.draft_status === 'needs_more_information') {
                        extras.push('Important: This is a preliminary draft. Please answer the follow-up questions below before final issue/filing.');
                    }

                    if (Array.isArray(data.follow_up_questions) && data.follow_up_questions.length > 0) {
                        extras.push('Follow-up Questions:\n' + data.follow_up_questions.map((q, i) => `${i + 1}. ${q}`).join('\n'));
                    }

                    if (Array.isArray(data.recommended_clauses) && data.recommended_clauses.length > 0) {
                        extras.push('Recommended Clauses to Add:\n' + data.recommended_clauses.map((c, i) => `${i + 1}. ${c}`).join('\n'));
                    }

                    if (typeof data.firmness_score === 'number') {
                        extras.push(`Draft Firmness Score: ${data.firmness_score}/100`);
                    }

                    const finalOutput = extras.length > 0
                        ? `${draftText}\n\n${extras.join('\n\n')}`
                        : draftText;
                    showResponse('drafting-response', finalOutput);
                } else {
                    showResponse('drafting-response', data.detail || 'An error occurred', true);
                }
            } catch (error) {
                if (error.name === 'AbortError') {
                    showResponse('drafting-response', 'Request timed out after 3 minutes. Document drafting can take time for complex documents. Please try again.', true);
                } else {
                    showResponse('drafting-response', `Connection error: ${error.message}\n\nPlease check your connection and try again.`, true);
                }
            }
        }

        async function searchCases() {
            const query = document.getElementById('case-query').value;
            const court = document.getElementById('court').value;
            const year = document.getElementById('year').value;

            if (!query.trim()) {
                alert('Please enter a search query');
                return;
            }

            showLoading('case-response');

            try {
                // Add timeout (120 seconds for case search)
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 120000);

                const body = { query: query };
                if (court) body.court = court;
                if (year) body.year = parseInt(year);

                const response = await fetch(`${API_BASE_URL}/search-cases`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(body),
                    signal: controller.signal
                });

                clearTimeout(timeoutId);
                const data = await response.json();

                if (response.ok) {
                    const casesText = data.cases.map((c, i) =>
                        `Case ${i + 1}:\n${c.content || JSON.stringify(c)}`
                    ).join('\n\n---\n\n');
                    showResponse('case-response', casesText);
                } else {
                    showResponse('case-response', data.detail || 'An error occurred', true);
                }
            } catch (error) {
                if (error.name === 'AbortError') {
                    showResponse('case-response', 'Request timed out after 2 minutes. Please try a simpler search query or try again.', true);
                } else {
                    showResponse('case-response', `Connection error: ${error.message}\n\nPlease check your connection and try again.`, true);
                }
            }
        }

        async function searchStatute() {
            const actName = document.getElementById('act-name').value;
            const section = document.getElementById('section').value;

            if (!actName.trim()) {
                alert('Please enter an act name');
                return;
            }

            showLoading('statute-response');

            try {
                // Add timeout (120 seconds for statute search)
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 120000);

                const body = { act_name: actName };
                if (section) body.section = section;

                const response = await fetch(`${API_BASE_URL}/search-statute`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(body),
                    signal: controller.signal
                });

                clearTimeout(timeoutId);
                
                // Parse JSON response - handle both success and error responses
                let data;
                try {
                    const responseText = await response.text();
                    if (!responseText || responseText.trim().length === 0) {
                        throw new Error('Server returned empty response');
                    }
                    data = JSON.parse(responseText);
                } catch (parseError) {
                    console.error('JSON parse error:', parseError);
                    console.error('Response status:', response.status);
                    throw new Error(`Server returned invalid response. Status: ${response.status}`);
                }

                if (response.ok) {
                    showResponse('statute-response', data.content || data.explanation);
                } else if (response.status === 503) {
                    // Handle 503 Service Unavailable gracefully - show the message from backend
                    const message = data.message || data.error || 'AI service is temporarily unavailable';
                    const content = data.content || data.explanation || message;
                    showResponse('statute-response', content, true);
                } else {
                    // Other errors
                    const errorMsg = data.detail || data.error || data.message || `Server error (${response.status})`;
                    showResponse('statute-response', `Error: ${errorMsg}`, true);
                }
            } catch (error) {
                if (error.name === 'AbortError') {
                    showResponse('statute-response', 'Request timed out after 2 minutes. Please try again.', true);
                } else {
                    showResponse('statute-response', `Connection error: ${error.message}\n\nPlease check your connection and try again.`, true);
                }
            }
        }
