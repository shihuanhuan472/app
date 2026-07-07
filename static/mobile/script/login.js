(function () {
    'use strict';

    const MOBILE_HOME = 'ai.html';
    const STORAGE_KEYS = ['token', 'refresh_token', 'user'];

    const state = {
        toastTimer: null
    };

    document.addEventListener('DOMContentLoaded', initLoginPage);

    function initLoginPage() {
        redirectIfAlreadyLoggedIn();
        bindPasswordToggle();
        bindLoginForm();
    }

    function redirectIfAlreadyLoggedIn() {
        const token = localStorage.getItem('token') || sessionStorage.getItem('token');
        const user = localStorage.getItem('user') || sessionStorage.getItem('user');

        if (token && user) {
            migrateAuthStorage();
            window.location.replace(MOBILE_HOME);
        }
    }

    function migrateAuthStorage() {
        STORAGE_KEYS.forEach((key) => {
            const localValue = localStorage.getItem(key);
            const sessionValue = sessionStorage.getItem(key);
            const value = localValue || sessionValue;
            if (value) {
                localStorage.setItem(key, value);
                sessionStorage.setItem(key, value);
            }
        });
    }

    function bindPasswordToggle() {
        const passwordInput = document.getElementById('password');
        const toggle = document.getElementById('passwordToggle');
        if (!passwordInput || !toggle) return;

        toggle.addEventListener('click', () => {
            const shouldShow = passwordInput.type === 'password';
            passwordInput.type = shouldShow ? 'text' : 'password';
            toggle.setAttribute('aria-label', shouldShow ? '隐藏密码' : '显示密码');
            toggle.innerHTML = shouldShow
                ? '<i class="far fa-eye-slash" aria-hidden="true"></i>'
                : '<i class="far fa-eye" aria-hidden="true"></i>';
        });
    }

    function bindLoginForm() {
        const form = document.getElementById('loginForm');
        if (!form) return;

        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            await handleLoginSubmit();
        });
    }

    async function handleLoginSubmit() {
        const username = getFieldValue('username');
        const password = getFieldValue('password');
        const role = getFieldValue('role');

        if (!username) {
            showError('请输入用户名');
            focusField('username');
            return;
        }

        if (!password) {
            showError('请输入密码（初始密码：123456）');
            focusField('password');
            return;
        }

        if (!role) {
            showError('请选择身份');
            focusField('role');
            return;
        }

        setLoading(true);
        hideError();

        try {
            const tokenData = await requestLogin({ username, password, role });
            saveLoginResult(tokenData || {});
            showToast('登录成功', 'success');
            window.setTimeout(() => {
                window.location.href = MOBILE_HOME;
            }, 450);
        } catch (error) {
            const message = `登录失败：${error.message || '网络请求失败，请检查后端服务是否启动'}`;
            showError(message);
            showToast(message, 'error');
        } finally {
            setLoading(false);
        }
    }

    async function requestLogin(payload) {
        if (typeof userAPI !== 'undefined' && userAPI && typeof userAPI.login === 'function') {
            return await userAPI.login(payload.username, payload.password, payload.role);
        }

        if (typeof API_CONFIG === 'undefined' || !API_CONFIG.BASE_URL || !API_CONFIG.ENDPOINTS) {
            throw new Error('接口配置未加载');
        }

        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.LOGIN}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });

        const text = await response.text();
        let data;
        try {
            data = JSON.parse(text);
        } catch (error) {
            throw new Error('服务器响应格式错误');
        }

        if (Number(data.code) !== 1) {
            throw new Error(resolveLoginErrorMessage(data));
        }

        return data.data || {};
    }

    function saveLoginResult(tokenData) {
        if (tokenData.access_token) {
            localStorage.setItem('token', tokenData.access_token);
            sessionStorage.setItem('token', tokenData.access_token);
        }

        if (tokenData.refresh_token) {
            localStorage.setItem('refresh_token', tokenData.refresh_token);
            sessionStorage.setItem('refresh_token', tokenData.refresh_token);
        }

        if (tokenData.user) {
            const userJson = JSON.stringify(tokenData.user);
            localStorage.setItem('user', userJson);
            sessionStorage.setItem('user', userJson);
        }
    }

    function resolveLoginErrorMessage(result) {
        const code = Number(result && result.code);
        const rawMsg = extractBackendMessage(result);

        if (code === 40110) return '登录失败：用户名或密码错误';
        if (code === 40311) return '登录失败：账号已被禁用，请联系管理员';
        if (code === 40111) return '登录失败：账号身份配置异常，请联系管理员';
        if (code === 40310) return '登录失败：账号权限配置异常，请联系管理员';

        if (rawMsg) return `登录失败：${rawMsg}`;
        return '登录失败：请稍后重试';
    }

    function extractBackendMessage(result) {
        if (!result || typeof result !== 'object') return '';
        return String(result.msg || result.message || result.detail || '').trim();
    }

    function getFieldValue(id) {
        const element = document.getElementById(id);
        return element ? element.value.trim() : '';
    }

    function focusField(id) {
        const element = document.getElementById(id);
        if (element) element.focus({ preventScroll: false });
    }

    function showError(message) {
        const errorElement = document.getElementById('loginError');
        if (!errorElement) return;
        errorElement.textContent = message;
        errorElement.classList.add('is-visible');
    }

    function hideError() {
        const errorElement = document.getElementById('loginError');
        if (!errorElement) return;
        errorElement.textContent = '';
        errorElement.classList.remove('is-visible');
    }

    function setLoading(isLoading) {
        const submitBtn = document.getElementById('submitBtn');
        if (!submitBtn) return;
        submitBtn.disabled = isLoading;
        submitBtn.classList.toggle('is-loading', isLoading);
        submitBtn.setAttribute('aria-busy', String(isLoading));
    }

    function showToast(message, type) {
        let toast = document.querySelector('.mobile-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.className = 'mobile-toast';
            toast.setAttribute('role', 'status');
            toast.setAttribute('aria-live', 'polite');
            document.body.appendChild(toast);
        }

        toast.className = `mobile-toast mobile-toast-${type || 'info'}`;
        toast.textContent = message;
        requestAnimationFrame(() => toast.classList.add('is-visible'));

        if (state.toastTimer) window.clearTimeout(state.toastTimer);
        state.toastTimer = window.setTimeout(() => {
            toast.classList.remove('is-visible');
        }, 2600);
    }
})();

