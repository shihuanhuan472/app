const Utils = {
  ROLE: {
    ADMIN: 0,
    TECHNICIAN: 1,
    REVIEWER: 2,
    MAINTENANCE: 3
  },

  showMessage(message, type = 'info') {
    const el = document.createElement('div');
    el.className = `message message-${type}`;
    el.innerHTML = `
      <div class="message-content">${message}</div>
      <button class="message-close" type="button" aria-label="关闭">×</button>
    `;

    document.body.appendChild(el);

    const remove = () => {
      el.style.animation = 'slideOut 0.25s ease forwards';
      setTimeout(() => el.remove(), 250);
    };

    const closeBtn = el.querySelector('.message-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', remove);
    }

    setTimeout(remove, 3000);
  },

  getToken() {
    return localStorage.getItem('token') || sessionStorage.getItem('token');
  },

  formatFileSize(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 Bytes';
    const units = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(2)} ${units[i]}`;
  },

  formatDate(date, format = 'YYYY-MM-DD') {
    const d = new Date(date);
    if (Number.isNaN(d.getTime())) return '';
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    const hours = String(d.getHours()).padStart(2, '0');
    const minutes = String(d.getMinutes()).padStart(2, '0');
    const seconds = String(d.getSeconds()).padStart(2, '0');

    return format
      .replace('YYYY', year)
      .replace('MM', month)
      .replace('DD', day)
      .replace('HH', hours)
      .replace('mm', minutes)
      .replace('ss', seconds);
  },

  normalizeRoleValue(role) {
    if (typeof role === 'number' && !Number.isNaN(role)) return role;
    if (typeof role !== 'string') return null;

    const value = role.trim().toLowerCase();
    if (value === '0' || value === 'admin' || value === '系统管理员') return this.ROLE.ADMIN;
    if (value === '1' || value === 'technician' || value === '技术员' || value === '维修工程师') return this.ROLE.TECHNICIAN;
    if (value === '2' || value === 'reviewer' || value === '审核员') return this.ROLE.REVIEWER;
    if (value === '3' || value === 'maintenance' || value === '运维员') return this.ROLE.MAINTENANCE;

    const parsed = Number(value);
    return Number.isNaN(parsed) ? null : parsed;
  },

  hasRole(userOrRole, ...roles) {
    const rawRole = userOrRole && typeof userOrRole === 'object'
      ? (userOrRole.role ?? userOrRole.role_id)
      : userOrRole;
    const currentRole = this.normalizeRoleValue(rawRole);
    if (currentRole === null || currentRole === undefined) return false;
    return roles.map(Number).includes(currentRole);
  },

  getRoleDisplay(userOrRole) {
    const rawRole = userOrRole && typeof userOrRole === 'object'
      ? (userOrRole.role ?? userOrRole.role_id)
      : userOrRole;
    const role = this.normalizeRoleValue(rawRole);

    const roleMap = {
      0: { label: '系统管理员', icon: 'A' },
      1: { label: '技术员', icon: 'T' },
      2: { label: '审核员', icon: 'R' },
      3: { label: '运维员', icon: 'M' }
    };

    const info = roleMap[role] || { label: '用户', icon: 'U' };
    return { value: role, label: info.label, icon: info.icon };
  },

  migrateToLocalStorage() {
    const token = sessionStorage.getItem('token');
    const refreshToken = sessionStorage.getItem('refresh_token');
    const user = sessionStorage.getItem('user');

    if (token && !localStorage.getItem('token')) localStorage.setItem('token', token);
    if (refreshToken && !localStorage.getItem('refresh_token')) localStorage.setItem('refresh_token', refreshToken);
    if (user && !localStorage.getItem('user')) localStorage.setItem('user', user);
  },

  checkLogin() {
    this.migrateToLocalStorage();
    const token = localStorage.getItem('token') || sessionStorage.getItem('token');
    const refreshToken = localStorage.getItem('refresh_token') || sessionStorage.getItem('refresh_token');

    if (!token && !refreshToken) {
      window.location.href = 'index.html';
      return null;
    }

    try {
      const userStr = localStorage.getItem('user') || sessionStorage.getItem('user');
      return userStr ? JSON.parse(userStr) : null;
    } catch (err) {
      console.error('解析用户信息失败:', err);
      window.location.href = 'index.html';
      return null;
    }
  },

  getCurrentUser() {
    try {
      const userStr = localStorage.getItem('user') || sessionStorage.getItem('user');
      return userStr ? JSON.parse(userStr) : null;
    } catch (err) {
      console.error('获取用户信息失败:', err);
      return null;
    }
  },

  loadUserInfo() {
    const user = this.getCurrentUser();
    if (!user) return;

    const avatar = document.getElementById('userAvatar');
    const name = document.getElementById('userName');
    const role = document.getElementById('userRole');

    const displayName = user.full_name || user.username || '用户';
    const roleInfo = this.getRoleDisplay(user);

    if (avatar) {
      avatar.textContent = displayName.charAt(0).toUpperCase();
      avatar.style.backgroundColor = roleInfo.value === this.ROLE.ADMIN ? '#dc2626' : '#4a9eff';
    }
    if (name) name.textContent = displayName;
    if (role) role.textContent = roleInfo.label;
  },

  updateMenuByRole(user) {
    if (!user) return;
    const isAdmin = this.hasRole(user, this.ROLE.ADMIN);

    const userManagementLink = document.querySelector('a[href="user-management.html"]');
    const myProfileLink = document.querySelector('a[href="user-profile.html"]');
    const aiAssistLink = document.querySelector('a[href="ai-assist.html"]');

    if (userManagementLink) userManagementLink.style.display = isAdmin ? 'flex' : 'none';
    if (myProfileLink) myProfileLink.style.display = 'flex';
    if (aiAssistLink) aiAssistLink.style.display = 'flex';

    document.querySelectorAll('.nav-item').forEach((item) => item.classList.remove('active'));
    const currentPath = window.location.pathname.split('/').pop();
    const currentLink = document.querySelector(`a[href="${currentPath}"]`);
    if (currentLink) currentLink.classList.add('active');
  },

  logout() {
    ['token', 'refresh_token', 'user', 'last_conversation_id'].forEach((key) => {
      localStorage.removeItem(key);
      sessionStorage.removeItem(key);
    });
    window.location.href = 'index.html';
  },

  debounce(func, wait) {
    let timeout;
    return function wrapped(...args) {
      clearTimeout(timeout);
      timeout = setTimeout(() => func.apply(this, args), wait);
    };
  },

  throttle(func, limit) {
    let inThrottle = false;
    return function wrapped(...args) {
      if (inThrottle) return;
      func.apply(this, args);
      inThrottle = true;
      setTimeout(() => {
        inThrottle = false;
      }, limit);
    };
  },

  getApiBaseUrl() {
    return '';
  },

  getAuthHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    const token = this.getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    return headers;
  },

  async refreshToken() {
    const refreshToken = localStorage.getItem('refresh_token') || sessionStorage.getItem('refresh_token');
    if (!refreshToken) throw new Error('没有可用的刷新令牌');

    const response = await fetch(`${this.getApiBaseUrl()}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken })
    });

    if (!response.ok) throw new Error(`刷新令牌失败: ${response.status}`);

    const result = await response.json();
    if (result.code !== 1 || !result.data?.access_token) {
      throw new Error(result.msg || '刷新令牌失败');
    }

    const newToken = result.data.access_token;
    localStorage.setItem('token', newToken);
    sessionStorage.setItem('token', newToken);
    return newToken;
  },

  async apiRequest(url, options = {}) {
    const requestOptions = {
      headers: this.getAuthHeaders(),
      ...options
    };

    if (requestOptions.body && typeof requestOptions.body !== 'string') {
      requestOptions.body = JSON.stringify(requestOptions.body);
    }

    let response = await fetch(url, requestOptions);

    if (response.status === 401) {
      try {
        const newToken = await this.refreshToken();
        requestOptions.headers = { ...requestOptions.headers, Authorization: `Bearer ${newToken}` };
        response = await fetch(url, requestOptions);
      } catch (err) {
        this.logout();
        throw new Error('登录已过期，请重新登录');
      }
    }

    if (response.status === 404) {
      throw new Error('接口不存在，请检查请求地址');
    }

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`HTTP错误: ${response.status} - ${text}`);
    }

    const result = await response.json();
    if (result.code === 1) return result.data;
    throw new Error(result.msg || '请求失败');
  },

  async getUserInfo() {
    return this.getCurrentUser();
  },

  formatMessageContent(content) {
    return String(content || '').replace(/\n/g, '<br>');
  },

  generateId() {
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  },

  truncateString(str, length) {
    const value = String(str || '');
    if (value.length <= length) return value;
    return `${value.slice(0, length)}...`;
  },

  getCurrentTime() {
    const now = new Date();
    return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  }
};

function initFormValidation() {
  const forms = document.querySelectorAll('form[data-validate]');
  forms.forEach((form) => {
    form.addEventListener('submit', (e) => {
      let isValid = true;
      const inputs = form.querySelectorAll('[required]');

      inputs.forEach((input) => {
        const value = (input.value || '').trim();

        if (!value) {
          isValid = false;
          highlightError(input, '该字段不能为空');
          return;
        }

        if (input.type === 'email') {
          const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
          if (!emailRegex.test(value)) {
            isValid = false;
            highlightError(input, '请输入有效的邮箱地址');
            return;
          }
        }

        if (input.type === 'password' && value.length < 6) {
          isValid = false;
          highlightError(input, '密码长度不能少于6位');
          return;
        }

        clearError(input);
      });

      if (!isValid) {
        e.preventDefault();
        Utils.showMessage('请先修正表单中的错误', 'error');
      }
    });
  });
}

function highlightError(input, message) {
  const formGroup = input.closest('.form-group');
  if (!formGroup) return;

  const existingError = formGroup.querySelector('.error-message');
  if (existingError) existingError.remove();

  input.classList.add('error');

  const errorEl = document.createElement('div');
  errorEl.className = 'error-message';
  errorEl.textContent = message;
  errorEl.style.color = '#ef4444';
  errorEl.style.fontSize = '12px';
  errorEl.style.marginTop = '4px';

  formGroup.appendChild(errorEl);
}

function clearError(input) {
  input.classList.remove('error');
  const formGroup = input.closest('.form-group');
  if (!formGroup) return;

  const errorMessage = formGroup.querySelector('.error-message');
  if (errorMessage) errorMessage.remove();
}

function handleImageFiles(files, previewContainer) {
  previewContainer.innerHTML = '';

  Array.from(files || []).forEach((file) => {
    if (!file.type.startsWith('image/')) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      const preview = document.createElement('div');
      preview.className = 'image-preview';
      preview.innerHTML = `
        <img src="${e.target.result}" alt="预览">
        <button type="button" class="btn-remove-image">×</button>
      `;

      const removeBtn = preview.querySelector('.btn-remove-image');
      if (removeBtn) {
        removeBtn.addEventListener('click', () => preview.remove());
      }

      previewContainer.appendChild(preview);
    };
    reader.readAsDataURL(file);
  });
}

function initImageUpload(uploadAreaId, previewContainerId) {
  const uploadArea = document.getElementById(uploadAreaId);
  const previewContainer = document.getElementById(previewContainerId);
  if (!uploadArea || !previewContainer) return null;

  const fileInput = document.createElement('input');
  fileInput.type = 'file';
  fileInput.multiple = true;
  fileInput.accept = 'image/*';
  fileInput.style.display = 'none';
  document.body.appendChild(fileInput);

  uploadArea.addEventListener('click', () => fileInput.click());

  uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.style.borderColor = '#4a9eff';
    uploadArea.style.backgroundColor = '#f8f9fa';
  });

  uploadArea.addEventListener('dragleave', () => {
    uploadArea.style.borderColor = '#d9e3f0';
    uploadArea.style.backgroundColor = 'transparent';
  });

  uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.style.borderColor = '#d9e3f0';
    uploadArea.style.backgroundColor = 'transparent';
    handleImageFiles(e.dataTransfer.files, previewContainer);
  });

  fileInput.addEventListener('change', () => handleImageFiles(fileInput.files, previewContainer));

  return {
    getSelectedFiles: () => fileInput.files
  };
}

document.addEventListener('DOMContentLoaded', () => {
  const currentPath = window.location.pathname;
  const isLoginPage = currentPath.includes('index.html') || currentPath === '/';

  if (!isLoginPage) {
    const user = Utils.checkLogin();
    if (user) {
      setTimeout(() => {
        Utils.loadUserInfo();
      }, 100);
    }
  }

  initFormValidation();
});

window.Utils = Utils;
window.initImageUpload = initImageUpload;
