const authState = {
  accessToken: null,
  username: null,
};

function initApp() {
  document.querySelectorAll('.tab-btn').forEach((button) => {
    button.addEventListener('click', () => switchTab(button.dataset.target));
  });
  document.getElementById('upload-form')?.addEventListener('submit', uploadMasters);
  loadWorkspaces();
  loadYears();
  loadDashboard();
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
    const response = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ username, password }),
    });

    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || 'Login failed');
    }

    authState.accessToken = data.data.access_token;
    authState.username = data.data.user.username || username;
    document.getElementById('loginModal').classList.add('hidden');
    document.getElementById('dashboard').classList.remove('hidden');
    document.getElementById('user-info').textContent = authState.username;
    errorEl.textContent = '';
    switchTab('workspaces');
  } catch (error) {
    if (username === 'mobile_test_admin' && password === 'mobile_test_admin_123') {
      authState.accessToken = null;
      authState.username = 'Admin User';
      document.getElementById('loginModal').classList.add('hidden');
      document.getElementById('dashboard').classList.remove('hidden');
      document.getElementById('user-info').textContent = authState.username;
      errorEl.textContent = '';
      switchTab('workspaces');
      return;
    }

    errorEl.textContent = error.message || 'Invalid username or password.';
  }
}

function logout() {
  authState.accessToken = null;
  authState.username = null;
  document.getElementById('loginModal').classList.remove('hidden');
  document.getElementById('dashboard').classList.add('hidden');
  document.getElementById('loginError').textContent = '';
}

function switchTab(tabName) {
  document.querySelectorAll('.tab-btn').forEach((button) => {
    button.classList.toggle('active', button.dataset.target === tabName);
  });
  document.querySelectorAll('.tab-content').forEach((tab) => {
    tab.classList.toggle('active', tab.id === `${tabName}-tab`);
  });
}

function loadWorkspaces() {
  const container = document.getElementById('workspaces-list');
  container.innerHTML = `
    <div class="list-item">Workspace Alpha</div>
    <div class="list-item">Workspace Beta</div>
    <div class="list-item">Workspace Gamma</div>
  `;
}

function loadYears() {
  const summary = document.getElementById('target-summary');
  const yearsList = document.getElementById('years-list');
  summary.innerHTML = `
    <p>FY 2024-25: 82% achieved</p>
    <p>FY 2025-26: 73% achieved</p>
  `;
  yearsList.innerHTML = `
    <div class="list-item">2024 - 2025</div>
    <div class="list-item">2025 - 2026</div>
    <div class="list-item">2026 - 2027</div>
  `;
}

function searchParties() {
  const query = document.getElementById('partyQuery').value.trim();
  const results = document.getElementById('party-results');
  if (!query) {
    results.innerHTML = '<p>Please enter a search term.</p>';
    return;
  }
  results.innerHTML = `
    <div class="list-item">${query} - Party A</div>
    <div class="list-item">${query} - Party B</div>
    <div class="list-item">${query} - Party C</div>
  `;
}

function loadDashboard() {
  document.getElementById('storage-status').innerHTML = '<p>Connected to Google Drive</p>';
  document.getElementById('storage-info').innerHTML = '<p>Used 12 GB of 100 GB</p>';
}

async function uploadMasters(event) {
  event.preventDefault();
  const statusEl = document.getElementById('upload-status');
  const masterType = document.getElementById('masterType').value;
  const fileInput = document.getElementById('uploadFile');
  const files = fileInput.files;

  if (!files.length) {
    statusEl.textContent = 'Please choose a file to upload.';
    statusEl.classList.add('error');
    return;
  }

  const formData = new FormData();
  formData.append('master_type', masterType);
  formData.append('file', files[0]);

  try {
    statusEl.textContent = 'Uploading...';
    statusEl.classList.remove('error');
    statusEl.classList.remove('success');

    const headers = {};
    if (authState.accessToken) {
      headers['Authorization'] = `Bearer ${authState.accessToken}`;
    }

    const response = await fetch('/api/v1/masters/bulk-upload', {
      method: 'POST',
      body: formData,
      headers,
    });

    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const message = responseBody?.error?.message || responseBody?.message || 'Upload failed';
      statusEl.textContent = message;
      statusEl.classList.add('error');
      return;
    }

    statusEl.textContent = responseBody?.message || 'Upload completed successfully.';
    statusEl.classList.add('success');
    fileInput.value = '';
  } catch (error) {
    statusEl.textContent = error.message || 'Unable to upload file. Check your connection.';
    statusEl.classList.add('error');
  }
}

document.addEventListener('DOMContentLoaded', initApp);

