// Auto-detect API URL based on environment
const API_BASE = (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
    ? 'http://localhost:8888/api/v1/diary'
    : '/api/v1/diary';

// --- Global State ---
let clients = [];
let cases = [];

// --- Profession Labels Config (Single Source of Truth) ---
const PROFESSION_LABELS = {
    advocate: {
        title: "⚖️ Advocate's Digital Diary",
        subtitle: "Track hearings, cases, clients & fees efficiently",
        kpis: {
            today: "Today's Hearings",
            pending: "Pending Tasks",
            active: "Active Cases",
            fees: "Fees Outstanding"
        },
        tabs: {
            dailyBoard: "📅 Daily Board",
            caseMaster: "📂 Case Master",
            clients: "👥 Clients",
            feeLedger: "💰 Fee Ledger"
        },
        primaryButton: "+ Log Hearing",
        dailyBoardTitle: "Upcoming Hearings",
        caseMasterTitle: "All Cases",
        newCaseButton: "+ New Case",
        tableHeaders: {
            date: "Date",
            reference: "Case No",
            authority: "Court",
            purpose: "Purpose",
            action: "Action"
        },
        caseTableHeaders: {
            reference: "Case No",
            client: "Client",
            authority: "Court",
            type: "Type",
            nextDate: "Next Hearing",
            status: "Status",
            view: "View"
        },
        emptyState: {
            dailyBoard: "No hearings scheduled.\nLog your next hearing to stay organised.",
            caseMaster: "No cases yet.\nAdd your first case to get started."
        }
    },
    chartered_accountant: {
        title: "📊 Chartered Accountant's Digital Diary",
        subtitle: "Manage compliances, notices, clients & assignments",
        kpis: {
            today: "Today's Due Compliances",
            pending: "Pending Filings / Replies",
            active: "Active Assignments",
            fees: "Fees Outstanding"
        },
        tabs: {
            dailyBoard: "📅 Daily Board",
            caseMaster: "📂 Engagements",
            clients: "👥 Clients",
            feeLedger: "💰 Fee Ledger"
        },
        primaryButton: "+ Log Compliance / Due Date",
        dailyBoardTitle: "Due Compliances",
        caseMasterTitle: "All Engagements",
        newCaseButton: "+ New Engagement",
        tableHeaders: {
            date: "Due Date",
            reference: "Ack / Ref No",
            authority: "Authority",
            purpose: "Compliance Type",
            action: "Action"
        },
        caseTableHeaders: {
            reference: "Ref / Ack No",
            client: "Client",
            authority: "Department",
            type: "Type",
            nextDate: "Due Date",
            status: "Status",
            view: "View"
        },
        emptyState: {
            dailyBoard: "No compliances due today.\nAdd upcoming filings to track deadlines and avoid penalties.",
            caseMaster: "No engagements yet.\nAdd your first engagement to get started."
        }
    }
};

// --- Profession Management ---
function normalizeProfession(value) {
    const normalized = String(value || '').toLowerCase().trim().replace(/\s+/g, '_');
    if (normalized === 'chartered_accountant' || normalized === 'ca') return 'chartered_accountant';
    return 'advocate';
}

function getProfession() {
    return normalizeProfession(localStorage.getItem('diary_profession') || 'advocate');
}

function setProfession(profession) {
    const normalized = normalizeProfession(profession);
    localStorage.setItem('diary_profession', normalized);
    updateUIForProfession();
}

function getLabels() {
    const profession = getProfession();
    return PROFESSION_LABELS[profession] || PROFESSION_LABELS.advocate;
}

// --- Profession Management Functions ---
function initializeProfessionSelector() {
    const selector = document.getElementById('profession-selector');
    if (selector) {
        selector.value = getProfession();
        selector.onchange = (e) => handleProfessionChange(e.target.value);
    }
}

function handleProfessionChange(profession) {
    setProfession(profession);
}

if (typeof window !== 'undefined') {
    window.handleProfessionChange = handleProfessionChange;
}

function updateUIForProfession() {
    const labels = getLabels();
    const profession = getProfession();
    
    // Update title and subtitle
    const titleEl = document.getElementById('diary-title');
    const subtitleEl = document.getElementById('diary-subtitle');
    if (titleEl) titleEl.textContent = labels.title;
    if (subtitleEl) subtitleEl.textContent = labels.subtitle;
    
    // Update KPI labels
    const widgetHeaders = document.querySelectorAll('#dashboard-widgets .widget h3');
    if (widgetHeaders.length >= 4) {
        widgetHeaders[0].textContent = labels.kpis.today;
        widgetHeaders[1].textContent = labels.kpis.pending;
        widgetHeaders[2].textContent = labels.kpis.active;
        widgetHeaders[3].textContent = labels.kpis.fees;
    }

    const kpiToday = document.getElementById('kpi-today');
    const kpiPending = document.getElementById('kpi-pending');
    const kpiActive = document.getElementById('kpi-active');
    const kpiFees = document.getElementById('kpi-fees');
    if (kpiToday) kpiToday.textContent = labels.kpis.today;
    if (kpiPending) kpiPending.textContent = labels.kpis.pending;
    if (kpiActive) kpiActive.textContent = labels.kpis.active;
    if (kpiFees) kpiFees.textContent = labels.kpis.fees;
    
    // Update tab labels
    const tabDailyBoard = document.getElementById('tab-daily-board');
    const tabCaseMaster = document.getElementById('tab-case-master');
    const tabClients = document.getElementById('tab-clients');
    const tabFeeLedger = document.getElementById('tab-fee-ledger');
    if (tabDailyBoard) tabDailyBoard.textContent = labels.tabs.dailyBoard;
    if (tabCaseMaster) tabCaseMaster.textContent = labels.tabs.caseMaster;
    if (tabClients) tabClients.textContent = labels.tabs.clients;
    if (tabFeeLedger) tabFeeLedger.textContent = labels.tabs.feeLedger;
    
    // Update primary action button
    const primaryBtn = document.getElementById('primary-action-btn');
    if (primaryBtn) primaryBtn.textContent = labels.primaryButton;
    
    // Update section titles
    const dailyBoardTitle = document.getElementById('daily-board-title');
    const caseMasterTitle = document.getElementById('case-master-title');
    const newCaseBtn = document.getElementById('new-case-btn');
    if (dailyBoardTitle) dailyBoardTitle.textContent = labels.dailyBoardTitle;
    if (caseMasterTitle) caseMasterTitle.textContent = labels.caseMasterTitle;
    if (newCaseBtn) newCaseBtn.textContent = labels.newCaseButton;
    
    // Update table headers
    const thDate = document.getElementById('th-date');
    const thReference = document.getElementById('th-reference');
    const thAuthority = document.getElementById('th-authority');
    const thPurpose = document.getElementById('th-purpose');
    const thAction = document.getElementById('th-action');
    if (thDate) thDate.textContent = labels.tableHeaders.date;
    if (thReference) thReference.textContent = labels.tableHeaders.reference;
    if (thAuthority) thAuthority.textContent = labels.tableHeaders.authority;
    if (thPurpose) thPurpose.textContent = labels.tableHeaders.purpose;
    if (thAction) thAction.textContent = labels.tableHeaders.action;
    
    // Update case table headers
    const caseThReference = document.getElementById('case-th-reference');
    const caseThClient = document.getElementById('case-th-client');
    const caseThAuthority = document.getElementById('case-th-authority');
    const caseThType = document.getElementById('case-th-type');
    const caseThNextDate = document.getElementById('case-th-next-date');
    const caseThStatus = document.getElementById('case-th-status');
    const caseThView = document.getElementById('case-th-view');
    if (caseThReference) caseThReference.textContent = labels.caseTableHeaders.reference;
    if (caseThClient) caseThClient.textContent = labels.caseTableHeaders.client;
    if (caseThAuthority) caseThAuthority.textContent = labels.caseTableHeaders.authority;
    if (caseThType) caseThType.textContent = labels.caseTableHeaders.type;
    if (caseThNextDate) caseThNextDate.textContent = labels.caseTableHeaders.nextDate;
    if (caseThStatus) caseThStatus.textContent = labels.caseTableHeaders.status;
    if (caseThView) caseThView.textContent = labels.caseTableHeaders.view;
    
    // Update search placeholder
    const caseSearchInput = document.getElementById('case-search-input');
    if (caseSearchInput) {
        caseSearchInput.placeholder = profession === 'advocate' 
            ? 'Search cases...' 
            : 'Search engagements...';
    }
    
    // Update modal titles and labels
    const modalCaseTitle = document.getElementById('modal-case-title');
    const modalHearingTitle = document.getElementById('modal-hearing-title');
    if (modalCaseTitle) {
        modalCaseTitle.textContent = profession === 'advocate' 
            ? 'New Case Entry' 
            : 'New Engagement Entry';
    }
    if (modalHearingTitle) {
        modalHearingTitle.textContent = profession === 'advocate' 
            ? 'Log New Hearing' 
            : 'Log Compliance / Due Date';
    }
    
    // Update form labels
    const labelCaseNumber = document.getElementById('label-case-number');
    const labelCourt = document.getElementById('label-court');
    const labelSelectCase = document.getElementById('label-select-case');
    const inputCaseNumber = document.getElementById('input-case-number');
    const inputCourt = document.getElementById('input-court');
    const labelHearingDate = document.getElementById('label-hearing-date');
    const labelNextDate = document.getElementById('label-next-date');
    const labelPurpose = document.getElementById('label-purpose');
    const textareaPurpose = document.getElementById('textarea-purpose');
    const btnSaveHearing = document.getElementById('btn-save-hearing');
    const labelCaseType = document.getElementById('label-case-type');
    const selectCaseType = document.getElementById('select-case-type');
    const labelFilingDate = document.getElementById('label-filing-date');
    const btnSaveCase = document.getElementById('btn-save-case');
    
    if (profession === 'chartered_accountant') {
        if (labelCaseNumber) labelCaseNumber.textContent = 'Ref / Ack No';
        if (labelCourt) labelCourt.textContent = 'Department / Authority';
        if (labelSelectCase) labelSelectCase.textContent = 'Select Engagement';
        if (inputCaseNumber) inputCaseNumber.placeholder = 'e.g. GSTIN-123456789, IT Notice No. 143(2)/2024';
        if (inputCourt) inputCourt.placeholder = 'e.g. GST Department, Income Tax Department';
        if (labelHearingDate) labelHearingDate.textContent = 'Due Date';
        if (labelNextDate) labelNextDate.textContent = 'Next Due Date';
        if (labelPurpose) labelPurpose.textContent = 'Compliance Type / Remarks';
        if (textareaPurpose) textareaPurpose.placeholder = 'e.g. GST Return Filing, IT Notice Reply, Audit Compliance...';
        if (btnSaveHearing) btnSaveHearing.textContent = 'Save Compliance';
        // Update case type options for CA
        if (selectCaseType) {
            selectCaseType.innerHTML = `
                <option value="GST">GST</option>
                <option value="Income Tax">Income Tax</option>
                <option value="Audit">Audit</option>
                <option value="MCA Compliance">MCA Compliance</option>
                <option value="TDS">TDS</option>
                <option value="Other">Other</option>
            `;
        }
        if (labelCaseType) labelCaseType.textContent = 'Compliance Type';
        if (labelFilingDate) labelFilingDate.textContent = 'Start Date / Period';
        if (btnSaveCase) btnSaveCase.textContent = 'Save Engagement';
    } else {
        if (labelCaseNumber) labelCaseNumber.textContent = 'Case Number';
        if (labelCourt) labelCourt.textContent = 'Court';
        if (labelSelectCase) labelSelectCase.textContent = 'Select Case';
        if (inputCaseNumber) inputCaseNumber.placeholder = 'e.g. WP 1234/2024';
        if (inputCourt) inputCourt.placeholder = 'e.g. High Court';
        if (labelHearingDate) labelHearingDate.textContent = 'Hearing Date';
        if (labelNextDate) labelNextDate.textContent = 'Next Date';
        if (labelPurpose) labelPurpose.textContent = 'Purpose / Order';
        if (textareaPurpose) textareaPurpose.placeholder = 'e.g. Evidence, Arguments...';
        if (btnSaveHearing) btnSaveHearing.textContent = 'Save Hearing';
        // Reset case type options for Advocate
        if (selectCaseType) {
            selectCaseType.innerHTML = `
                <option value="Civil">Civil</option>
                <option value="Criminal">Criminal</option>
                <option value="Corporate">Corporate</option>
                <option value="Family">Family</option>
            `;
        }
        if (labelCaseType) labelCaseType.textContent = 'Case Type';
        if (labelFilingDate) labelFilingDate.textContent = 'Filing Date';
        if (btnSaveCase) btnSaveCase.textContent = 'Save Case';
    }
    
    // Reload data to reflect profession change
    if (document.getElementById('daily-board')?.classList.contains('active')) {
        loadDailyBoard();
    }
    if (document.getElementById('case-master')?.classList.contains('active')) {
        loadCaseMaster();
    }
    loadDashboard();
}

// --- Init ---
document.addEventListener('DOMContentLoaded', () => {
    initializeProfessionSelector();
    updateUIForProfession();
    loadDashboard();
    loadDailyBoard();
    loadClients(); // Prefetch for selects
});

// --- Navigation ---
function switchTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-tab').forEach(el => el.classList.remove('active'));

    document.getElementById(tabId).classList.add('active');
    document.querySelector(`button[onclick="switchTab('${tabId}')"]`).classList.add('active');

    if (tabId === 'daily-board') loadDailyBoard();
    if (tabId === 'case-master') loadCaseMaster();
    if (tabId === 'clients') loadClientsTable();
    if (tabId === 'fee-ledger') loadFeeLedger();
}

// --- Modals ---
function openModal(id) { document.getElementById(id).classList.add('show'); }
function closeModal(id) { document.getElementById(id).classList.remove('show'); }
window.onclick = function (event) {
    if (event.target.classList.contains('modal')) event.target.classList.remove('show');
}

async function openCaseModal() {
    populateClientSelect(); // Sync
    openModal('modal-case');
}
function openClientModal() { openModal('modal-client'); }

// --- API Calls & Renders ---

// 1. Dashboard
// 1. Dashboard
async function loadDashboard() {
    try {
        const res = await fetch(`${API_BASE}/dashboard`);
        if (!res.ok) throw new Error("Dashboard API failed");

        const data = await res.json();

        // Safety checks and formatting
        document.getElementById('stat-hearings').innerText = data.hearings_today !== undefined ? data.hearings_today : 0;
        document.getElementById('stat-cases').innerText = data.cases_pending !== undefined ? data.cases_pending : 0;
        document.getElementById('stat-tasks').innerText = data.tasks_due_today !== undefined ? data.tasks_due_today : 0;

        const feesVal = data.fees_outstanding !== undefined ? data.fees_outstanding : 0;
        document.getElementById('stat-fees').innerText = feesVal.toLocaleString('en-IN', { style: 'currency', currency: 'INR' });

    } catch (e) {
        console.error("Error loading dashboard", e);
        document.getElementById('stat-hearings').innerText = "-";
        document.getElementById('stat-cases').innerText = "-";
        document.getElementById('stat-tasks').innerText = "-";
        document.getElementById('stat-fees').innerText = "₹-";
    }
}

// Global Log Hearing Modal
async function openHearingModal() {
    // Ensure cases are loaded for the dropdown
    if (cases.length === 0) {
        // Fetch specific for dropdown if not already loaded
        const res = await fetch(`${API_BASE}/cases`);
        cases = await res.json();
    }

    const sel = document.getElementById('hearing-case-select');
    sel.innerHTML = '<option value="">Select Case...</option>';
    cases.forEach(c => {
        sel.innerHTML += `<option value="${c.id}">${c.case_number} - ${c.court}</option>`;
    });

    openModal('modal-hearing');
}

async function handleLogHearingGlobal(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = Object.fromEntries(fd);
    body.case_id = parseInt(body.case_id);

    try {
        const res = await fetch(`${API_BASE}/hearings`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (res.ok) {
            closeModal('modal-hearing');
            e.target.reset();
            loadDashboard(); // Refresh stats
            if (document.getElementById('daily-board').classList.contains('active')) loadDailyBoard();
        }
    } catch (err) { console.error(err); }
}

// 2. Clients
async function loadClients() {
    const res = await fetch(`${API_BASE}/clients`);
    clients = await res.json();
}

function populateClientSelect() {
    const sel = document.getElementById('case-client-select');
    sel.innerHTML = '<option value="">Select Client...</option>';
    clients.forEach(c => {
        sel.innerHTML += `<option value="${c.id}">${c.full_name}</option>`;
    });
}

async function handleCreateClient(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = Object.fromEntries(fd);

    await fetch(`${API_BASE}/clients`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });

    closeModal('modal-client');
    e.target.reset();
    loadClients(); // Reload global list
    if (document.getElementById('clients').classList.contains('active')) loadClientsTable();
}

async function loadClientsTable() {
    await loadClients();
    const tbody = document.getElementById('clients-list');
    tbody.innerHTML = clients.map(c => `
        <tr>
            <td>${c.full_name}</td>
            <td>${c.mobile || '-'}</td>
            <td>${c.email || '-'}</td>
            <td><button class="btn btn-secondary" onclick="viewClient(${c.id})">Details</button></td>
        </tr>
    `).join('');
}


function viewClient(id) {
    alert("Client Details: Implementation Pending\\nID: " + id);
}

// 3. Cases
async function loadCaseMaster() {
    const res = await fetch(`${API_BASE}/cases`);
    cases = await res.json();

    const tbody = document.getElementById('cases-list');
    tbody.innerHTML = cases.map(c => {
        const clientName = clients.find(cl => cl.id === c.client_id)?.full_name || 'Unknown';
        return `
        <tr>
            <td><b>${c.case_number}</b></td>
            <td>${clientName}</td>
            <td>${c.court}</td>
            <td>${c.case_type}</td>
            <td>${c.next_hearing || '-'}</td>
            <td><span class="status-badge status-${c.status === 'Active' ? 'active' : 'disposed'}">${c.status}</span></td>
            <td><button class="btn" onclick="openCaseDetail(${c.id})">View</button></td>
        </tr>
        `;
    }).join('');
}

async function handleCreateCase(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = Object.fromEntries(fd);

    await fetch(`${API_BASE}/cases`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });

    closeModal('modal-case');
    e.target.reset();
    loadCaseMaster();
}

async function openCaseDetail(id) {
    // Ideally fetch details properly
    const res = await fetch(`${API_BASE}/cases/${id}`);
    const caseData = await res.json();
    openModal('modal-case-detail');

    const clientName = clients.find(cl => cl.id === caseData.client_id)?.full_name || 'Unknown';

    document.getElementById('case-detail-content').innerHTML = `
        <div class="case-detail-header">
            <div>
                <h3>${caseData.case_number}</h3>
                <p>Court: ${caseData.court}</p>
            </div>
            <div>
                <p>Client: <b>${clientName}</b></p>
                <p>Status: ${caseData.status}</p>
            </div>
        </div>
        
        <!-- Tabs inside Modal -->
        <div style="margin-bottom: 20px;">
           <button class="btn" onclick="loadCaseHearings(${id})">Hearings</button>
           <button class="btn btn-secondary">Tasks</button>
           <button class="btn btn-secondary">Fees</button>
        </div>
        
        <div id="case-inner-content">
            <p>Select a tab to view details.</p>
        </div>
    `;
    loadCaseHearings(id); // Default load hearings
}


// 4. Hearings / Daily Board
async function loadDailyBoard() {
    const res = await fetch(`${API_BASE}/dashboard`); // Contains upcoming hearings
    const data = await res.json();

    const tbody = document.getElementById('hearings-list');
    tbody.innerHTML = data.upcoming_hearings.map(h => `
        <tr>
            <td>${h.hearing_date}</td>
            <td>Case #${h.case_id}</td>
            <td>-</td>
            <td>${h.purpose || 'Hearing'}</td>
            <td><button class="btn btn-secondary">Update</button></td>
        </tr>
    `).join('');
}

async function loadCaseHearings(caseId) {
    const res = await fetch(`${API_BASE}/hearings?case_id=${caseId}`);
    const hearings = await res.json();

    document.getElementById('case-inner-content').innerHTML = `
        <h4>Hearing History</h4>
        <table style="font-size: 0.9em;">
            <thead><tr><th>Date</th><th>Purpose</th><th>Order</th><th>Next Date</th></tr></thead>
            <tbody>
                ${hearings.map(h => `
                    <tr>
                        <td>${h.hearing_date}</td>
                        <td>${h.purpose || '-'}</td>
                        <td>${h.order_passed || '-'}</td>
                        <td>${h.next_date || '-'}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
        
        <div style="margin-top: 20px; border-top: 1px solid #eee; padding-top: 15px;">
            <h4>Log New Hearing</h4>
            <form onsubmit="handleLogHearing(event, ${caseId})">
                <input type="hidden" name="case_id" value="${caseId}">
                <div class="form-grid">
                    <div class="form-group">
                        <label>Date</label>
                        <input type="date" name="hearing_date" required>
                    </div>
                    <div class="form-group">
                        <label>Next Hearing Date</label>
                        <input type="date" name="next_date">
                    </div>
                </div>
                 <div class="form-group">
                    <label>Order / Summary</label>
                    <textarea name="remarks" placeholder="What happened inside the court?"></textarea>
                </div>
                <button type="submit" class="btn">Update Diary</button>
            </form>
        </div>
    `;
}

async function handleLogHearing(e, caseId) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = Object.fromEntries(fd);
    body.case_id = parseInt(caseId); // Ensure int

    await fetch(`${API_BASE}/hearings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });

    loadCaseHearings(caseId); // Reload inner list
    loadCaseHearings(caseId); // Reload inner list
}

// 5. Fee Ledger
// 5. Fee Ledger
async function loadFeeLedger() {
    try {
        const tbody = document.getElementById('fees-list');
        // Placeholder for now as per user instruction, but allowing new entries
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center">Select a Case to view Fees or Log a new Fee. Global Ledger coming soon.</td></tr>';
    } catch (e) { console.error(e); }
}

async function openFeeModal() {
    // Ensure cases loaded
    if (cases.length === 0) {
        try {
            const res = await fetch(`${API_BASE}/cases`);
            cases = await res.json();
        } catch (e) { console.error("Error loading cases", e); }
    }

    const sel = document.getElementById('fee-case-select');
    sel.innerHTML = '<option value="">Select Case...</option>';
    cases.forEach(c => {
        sel.innerHTML += `<option value="${c.id}">${c.case_number} - ${c.court}</option>`;
    });

    openModal('modal-fee');
}

async function handleLogFee(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = Object.fromEntries(fd);
    body.case_id = parseInt(body.case_id);
    body.amount_billed = parseFloat(body.amount_billed || 0);
    body.amount_received = parseFloat(body.amount_received || 0);

    try {
        await fetch(`${API_BASE}/fees`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        closeModal('modal-fee');
        e.target.reset();
        loadDashboard();
        loadFeeLedger();
    } catch (err) { console.error(err); }
}



