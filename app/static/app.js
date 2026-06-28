const VALID_USERNAME = 'mobile_test_admin';
const VALID_PASSWORD = 'mobile_test_admin_123';

function initApp() {
  document.querySelectorAll('.tab-btn').forEach((button) => {
    button.addEventListener('click', () => switchTab(button.dataset.target));
  });
  loadWorkspaces();
  loadYears();
  loadDashboard();
}

function login() {
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value.trim();
  const errorEl = document.getElementById('loginError');

  if (username === VALID_USERNAME && password === VALID_PASSWORD) {
    document.getElementById('loginModal').classList.add('hidden');
    document.getElementById('dashboard').classList.remove('hidden');
    document.getElementById('user-info').textContent = 'Admin User';
    errorEl.textContent = '';
    switchTab('workspaces');
  } else {
    errorEl.textContent = 'Invalid username or password.';
  }
}

function logout() {
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

document.addEventListener('DOMContentLoaded', initApp);

