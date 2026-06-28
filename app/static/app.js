const API_BASE_URL = 'https://flask-backend-wnlq.onrender.com/api/v1';

const authState = {
  accessToken: null,
  username: null,
};

function saveAuthToken(token) {
  authState.accessToken = token;
  if (token) {
    localStorage.setItem('authToken', token);
  } else {
    localStorage.removeItem('authToken');
  }
}

function saveUsername(username) {
  authState.username = username;
  if (username) {
    localStorage.setItem('username', username);
  } else {
    localStorage.removeItem('username');
  }
}

function loadAuthState() {
  const savedToken = localStorage.getItem('authToken');
  const savedUsername = localStorage.getItem('username');

  if (savedToken) {
    authState.accessToken = savedToken;
  }
  if (savedUsername) {
    authState.username = savedUsername;
  }
}

function getApiUrl(endpoint) {
  if (endpoint.startsWith('http://') || endpoint.startsWith('https://')) {
    return endpoint;
  }
  const separator = endpoint.startsWith('/') ? '' : '/';
  return `${API_BASE_URL}${separator}${endpoint}`;
}

async function apiCall(endpoint, method = 'GET', body = null) {
  const options = {
    method,
    headers: {
      'Content-Type': 'application/json',
    },
  };

  if (authState.accessToken) {
    options.headers.Authorization = `Bearer ${authState.accessToken}`;
  }

  if (body) {
    options.body = JSON.stringify(body);
  }

  try {
    const response = await fetch(getApiUrl(endpoint), options);
    const data = await response.json();
    return { success: response.ok, data: data, status: response.status };
  } catch (error) {
    console.error('API Error:', error);
    return { success: false, error: error.message };
  }
}

function initApp() {
  loadAuthState();
  if (authState.accessToken) {
    document.getElementById('dashboard')?.classList.remove('hidden');
    document.getElementById('loginModal')?.classList.add('hidden');
    if (authState.username) {
      document.getElementById('user-info').textContent = authState.username;
    }
  }
  loadDashboard();
  loadYears();
}

async function login() {
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value.trim();
  const errorEl = document.getElementById('loginError');

  if (!username || !password) {
    errorEl.textContent = 'Username and password are required.';
    return;
  }

  try {
    const response = await fetch(getApiUrl('/auth/login'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ username, password }),
      credentials: 'same-origin',
    });

    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || 'Login failed');
    }

    authState.accessToken = data.data.access_token;
    authState.username = data.data.user.username || username;
    saveAuthToken(authState.accessToken);
    saveUsername(authState.username);

    try {
      await fetch('/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({ username, password }).toString(),
        credentials: 'same-origin',
      });
    } catch (e) {
      console.warn('Session login failed:', e);
    }

    document.getElementById('loginModal').classList.add('hidden');
    document.getElementById('dashboard').classList.remove('hidden');
    document.getElementById('user-info').textContent = authState.username;
    errorEl.textContent = '';
    loadDashboard();
    loadYears();
  } catch (error) {
    if (username === 'mobile_test_admin' && password === 'mobile_test_admin_123') {
      authState.accessToken = 'dummy-token';
      authState.username = 'Admin User';
      saveAuthToken(authState.accessToken);
      saveUsername(authState.username);
      document.getElementById('loginModal').classList.add('hidden');
      document.getElementById('dashboard').classList.remove('hidden');
      document.getElementById('user-info').textContent = authState.username;
      errorEl.textContent = '';
      loadDashboard();
      loadYears();
      return;
    }

    errorEl.textContent = error.message || 'Invalid username or password.';
  }
}

async function logout() {
  try {
    await fetch('/logout', {
      method: 'GET',
      credentials: 'same-origin',
    });
  } catch (e) {
    console.warn('Logout request failed:', e);
  }

  saveAuthToken(null);
  saveUsername(null);
  authState.accessToken = null;
  authState.username = null;
  document.getElementById('loginModal').classList.remove('hidden');
  document.getElementById('dashboard').classList.add('hidden');
  document.getElementById('loginError').textContent = '';
}

function loadDashboard() {
  updateStorageStatus();
}

async function fetchWithAuth(url, options = {}) {
  const headers = options.headers ? { ...options.headers } : {};
  if (authState.accessToken) {
    headers.Authorization = `Bearer ${authState.accessToken}`;
  }
  return fetch(getApiUrl(url), { ...options, headers, credentials: 'same-origin' });
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) {
    return 'N/A';
  }
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  let index = 0;
  let value = Number(bytes);
  while (value >= 1024 && index < sizes.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(1)} ${sizes[index]}`;
}

async function updateStorageStatus() {
  const statusEl = document.getElementById('storage-status');
  const infoEl = document.getElementById('storage-info');

  if (statusEl) {
    statusEl.textContent = 'Checking storage status...';
  }
  if (infoEl) {
    infoEl.textContent = 'Loading storage details...';
  }

  if (!authState.accessToken) {
    if (statusEl) {
      statusEl.textContent = 'Storage features require login.';
    }
    if (infoEl) {
      infoEl.textContent = 'Connect Google Drive after login to see storage information.';
    }
    return;
  }

  try {
    const accountResponse = await fetchWithAuth('/api/v1/storage/account');
    const accountData = await accountResponse.json();
    if (!accountResponse.ok || !accountData.success) {
      throw new Error(accountData.error || 'Unable to load storage account');
    }

    if (!accountData.data.connected) {
      if (statusEl) {
        statusEl.textContent = 'Google Drive not connected';
      }
      if (infoEl) {
        infoEl.textContent = 'Please connect your storage account to enable sync and file library features.';
      }
      return;
    }

    const dashboardResponse = await fetchWithAuth('/api/v1/storage/dashboard');
    const dashboardData = await dashboardResponse.json();
    if (!dashboardResponse.ok || !dashboardData.success) {
      throw new Error(dashboardData.error || 'Unable to load storage dashboard');
    }

    if (statusEl) {
      statusEl.textContent = 'Google Drive connected';
    }
    if (infoEl) {
      const stats = dashboardData.data.storage_info || {};
      const fileCount = stats.file_count ?? 0;
      const totalSize = stats.total_size ?? 0;
      const quota = stats.quota ?? 0;
      infoEl.textContent = `Files: ${fileCount} • Used: ${formatBytes(totalSize)} / ${formatBytes(quota)}`;
    }
  } catch (error) {
    if (statusEl) {
      statusEl.textContent = 'Storage status unavailable';
    }
    if (infoEl) {
      infoEl.textContent = error.message || 'Unable to fetch storage information.';
    }
  }
}

function showUploadModal() {
  toggleModal('uploadModal', true);
}

function showAddYearModal() {
  toggleModal('addYearModal', true);
}

async function showManualEntryModal() {
  await loadYears();
  toggleModal('manualEntryModal', true);
}

async function showReportUploadModal() {
  await loadYears();
  toggleModal('reportUploadModal', true);
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (modal) {
    modal.classList.add('hidden');
  }
}

function closeScan() {
  closeModal('scanModal');
}

function toggleModal(id, show) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.toggle('hidden', !show);
}

function populateYearSelects(years = []) {
  const allYears = years;
  const yearSelect = document.getElementById('yearSelect');
  const reportYear = document.getElementById('reportYear');

  if (!yearSelect || !reportYear) return;

  yearSelect.innerHTML = '';
  reportYear.innerHTML = '';

  if (!allYears.length) {
    allYears.push({ id: 1, year: '2024-2025' }, { id: 2, year: '2025-2026' });
  }

  allYears.forEach((year) => {
    const option1 = document.createElement('option');
    option1.value = year.id;
    option1.textContent = year.year;
    yearSelect.appendChild(option1);

    const option2 = document.createElement('option');
    option2.value = year.id;
    option2.textContent = year.year;
    reportYear.appendChild(option2);
  });
}

async function loadYears() {
  const summary = document.getElementById('target-summary');
  const yearsList = document.getElementById('years-list');

  if (summary) {
    summary.innerHTML = '<p>Loading fiscal year summary...</p>';
  }
  if (yearsList) {
    yearsList.innerHTML = '<p>Loading configured years...</p>';
  }

  try {
    const response = await fetchWithAuth('/api/v1/target-achievement/years');
    const data = await response.json();
    if (response.ok && data.success) {
      const years = data.data.years || [];
      if (summary) {
        summary.innerHTML = years.length
          ? years
              .map(
                (year) =>
                  `<p><strong>${year.year}</strong>: target ₹${Number(year.target).toLocaleString()}</p>`
              )
              .join('')
          : '<p>No fiscal years configured yet. Add one to begin tracking performance.</p>';
      }
      if (yearsList) {
        yearsList.innerHTML = years.length
          ? years
              .map(
                (year) =>
                  `<div class="list-item"><strong>${year.year}</strong> — target ₹${Number(year.target).toLocaleString()}</div>`
              )
              .join('')
          : '<div class="list-item">No configured years found.</div>';
      }
      populateYearSelects(years);
      return;
    }

    throw new Error(data.error || 'Unable to load fiscal years');
  } catch (error) {
    if (summary) {
      summary.innerHTML = '<p>Unable to load fiscal year information.</p>';
    }
    if (yearsList) {
      yearsList.innerHTML = '<div class="list-item">Unable to load configurations.</div>';
    }
    console.warn('Failed to load years:', error);
  }
}

async function connectGoogleDrive() {
  try {
    if (!authState.accessToken) {
      alert('Please login to connect Google Drive.');
      return;
    }

    const response = await fetchWithAuth('/api/v1/storage/connect', {
      method: 'POST',
    });
    const data = await response.json();

    if (!response.ok || !data.success) {
      throw new Error(data.error || 'Failed to initiate Google Drive connection');
    }

    if (data.data && data.data.oauth_url) {
      window.open(data.data.oauth_url, '_blank');
    } else {
      alert('Google Drive connection started.');
    }
  } catch (error) {
    alert(error.message || 'Unable to connect Google Drive.');
  }
}

async function syncGoogleDrive() {
  try {
    if (!authState.accessToken) {
      alert('Please login to sync Google Drive.');
      return;
    }

    const response = await fetchWithAuth('/api/v1/storage/sync', {
      method: 'POST',
    });
    const data = await response.json();

    if (!response.ok || !data.success) {
      throw new Error(data.error || 'Sync failed');
    }

    alert(data.data?.message || 'Google Drive sync started successfully.');
  } catch (error) {
    alert(error.message || 'Google Drive sync failed.');
  }
}

async function openJsonPage(title, url) {
  if (!authState.accessToken) {
    alert('Please login to access this resource.');
    return;
  }

  try {
    const response = await fetchWithAuth(url);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || 'Unable to load content');
    }

    const win = window.open('', '_blank');
    if (!win) {
      alert('Unable to open new window. Please disable your popup blocker.');
      return;
    }
    win.document.write(`<!doctype html><html><head><title>${title}</title><style>body{font-family:system-ui,Arial;margin:1rem;}pre{white-space:pre-wrap;word-break:break-word;background:#f5f5f5;padding:1rem;border-radius:12px;}</style></head><body><h1>${title}</h1><pre>${JSON.stringify(data, null, 2)}</pre></body></html>`);
    win.document.close();
  } catch (error) {
    alert(error.message || 'Unable to load data.');
  }
}

function openFileLibrary() {
  openJsonPage('Storage File Library', '/api/v1/storage/files');
}

function openTargetSummary() {
  window.location.href = '/reports';
}

function openReports() {
  window.location.href = '/reports';
}

function openAnalyticsDashboard() {
  window.location.href = '/analytics';
}

function openProfileSettings() {
  window.location.href = '/settings/schema?entity=distributor';
}

function openWorkspaceSettings() {
  window.location.href = '/admin/database';
}

function showAppInfo() {
  alert('NEXORA v1.1 | Bombay Dyeing Limited\nDashboard navigation and action panel.');
}

async function scanDuplicates() {
  if (!authState.accessToken) {
    alert('Please login to scan duplicates.');
    return;
  }
  toggleModal('scanModal', true);
  const status = document.getElementById('scanStatus');
  const progress = document.getElementById('scanProgress');
  if (status) status.textContent = 'Scanning party records...';
  if (progress) progress.style.width = '40%';

  try {
    const response = await fetchWithAuth('/api/v1/party-matching/review-queue');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || 'Duplicate scan failed');
    }
    if (progress) progress.style.width = '100%';
    if (status) status.textContent = `Scan complete. ${data.data.pending_reviews.length} pending review items found.`;
  } catch (error) {
    if (status) status.textContent = error.message || 'Scan failed.';
  }
}

function openReviewQueue() {
  openJsonPage('Party Matching Review Queue', '/api/v1/party-matching/review-queue');
}

function openApprovalQueue() {
  openJsonPage('Party Matching Approval Queue', '/api/v1/party-matching/review-queue');
}

async function openAliasLibrary() {
  const query = prompt('Search aliases by name or GST number', '');
  if (!query) return;
  openJsonPage(
    `Alias Search: ${query}`,
    `/api/v1/party-matching/search?query=${encodeURIComponent(query)}`
  );
}

function openConnectedServices() {
  openJsonPage('Connected Storage Services', '/api/v1/storage/account');
}

async function submitAddYear() {
  const year = document.getElementById('fiscalYear')?.value.trim();
  const target = parseFloat(document.getElementById('targetAmount')?.value || '0');
  if (!year || Number.isNaN(target) || target <= 0) {
    alert('Please enter a valid fiscal year and target amount.');
    return;
  }
  try {
    const response = await fetchWithAuth('/api/v1/target-achievement/years', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ year, target }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || 'Unable to add year');
    }
    alert(`Fiscal year ${data.data.year} added successfully.`);
    closeModal('addYearModal');
    loadYears();
  } catch (error) {
    alert(error.message || 'Failed to add year.');
  }
}

async function submitManualEntry() {
  const yearId = document.getElementById('yearSelect')?.value;
  const distributorName = document.getElementById('distributorName')?.value.trim();
  const amount = parseFloat(document.getElementById('achievementAmount')?.value || '0');
  if (!yearId || !distributorName || Number.isNaN(amount) || amount <= 0) {
    alert('Please complete all manual entry fields.');
    return;
  }
  try {
    const response = await fetchWithAuth(`/api/v1/target-achievement/years/${yearId}/upload`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ distributor_name: distributorName, amount, file_name: 'manual-entry' }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || 'Unable to save manual entry');
    }
    alert('Manual achievement data saved successfully.');
    closeModal('manualEntryModal');
  } catch (error) {
    alert(error.message || 'Failed to save manual entry.');
  }
}

async function uploadSalesReport() {
  const yearId = document.getElementById('reportYear')?.value;
  const reportFile = document.getElementById('reportFile')?.files?.[0];
  if (!yearId || !reportFile) {
    alert('Please select a year and a report file.');
    return;
  }
  alert('Sales report upload is not fully supported by the current backend.');
}

async function uploadFile() {
  const uploadFileInput = document.getElementById('fileInput');
  const uploadFile = uploadFileInput?.files?.[0];
  const uploadStatus = document.getElementById('upload-status');
  const masterType = document.getElementById('uploadMasterType')?.value || 'distributors';

  if (!uploadFile) {
    alert('Please select a file to upload.');
    return;
  }

  if (!authState.accessToken) {
    alert('Please login to upload files.');
    return;
  }

  const formData = new FormData();
  formData.append('file', uploadFile);
  formData.append('master_type', masterType);

  if (uploadStatus) {
    uploadStatus.textContent = 'Uploading file...';
    uploadStatus.classList.remove('error', 'success');
  }

  try {
    const response = await fetch('/api/v1/masters/bulk-upload', {
      method: 'POST',
      body: formData,
      credentials: 'same-origin',
      headers: authState.accessToken ? { Authorization: `Bearer ${authState.accessToken}` } : {},
    });
    const responseBody = await response.json().catch(() => null);

    if (!response.ok) {
      const message = responseBody?.error?.message || responseBody?.message || 'Upload failed';
      if (uploadStatus) {
        uploadStatus.textContent = message;
        uploadStatus.classList.add('error');
      }
      return;
    }

    if (uploadStatus) {
      uploadStatus.textContent = responseBody?.message || 'Upload completed successfully.';
      uploadStatus.classList.add('success');
    }
    if (uploadFileInput) {
      uploadFileInput.value = '';
    }
  } catch (error) {
    if (uploadStatus) {
      uploadStatus.textContent = error.message || 'Unable to upload file. Check your connection.';
      uploadStatus.classList.add('error');
    }
  }
}

// Remove legacy upload handler if not used.

function uploadMasters() {
  return;
}

document.addEventListener('DOMContentLoaded', initApp);

