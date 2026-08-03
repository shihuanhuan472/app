(function () {
    'use strict';

    const form = document.getElementById('registerForm');
    const button = document.getElementById('registerBtn');
    const errorElement = document.getElementById('registerError');
    const successElement = document.getElementById('registerSuccess');

    form.addEventListener('submit', async function (event) {
        event.preventDefault();
        const payload = {
            username: valueOf('username'),
            full_name: valueOf('fullName'),
            phone: valueOf('phone'),
            email: valueOf('email') || null,
            department: valueOf('department') || null,
            password: document.getElementById('password').value,
            confirm_password: document.getElementById('confirmPassword').value
        };

        hideError();
        if (!payload.username || !payload.full_name || !payload.phone || !payload.password) {
            showError('请完整填写必填信息');
            return;
        }
        if (payload.username.length < 3) {
            showError('用户名至少需要 3 个字符');
            return;
        }
        if (!/^1[3-9]\d{9}$/.test(payload.phone)) {
            showError('请输入有效的手机号码');
            return;
        }
        if (payload.password.length < 6) {
            showError('密码至少需要 6 位');
            return;
        }
        if (payload.password !== payload.confirm_password) {
            showError('两次输入的密码不一致');
            return;
        }

        setLoading(true);
        try {
            await userAPI.register(payload);
            form.style.display = 'none';
            successElement.classList.add('is-visible');
        } catch (requestError) {
            showError(requestError.message || '注册失败，请稍后重试');
        } finally {
            setLoading(false);
        }
    });

    function valueOf(id) {
        return document.getElementById(id).value.trim();
    }

    function showError(message) {
        errorElement.textContent = message;
        errorElement.classList.add('is-visible');
    }

    function hideError() {
        errorElement.textContent = '';
        errorElement.classList.remove('is-visible');
    }

    function setLoading(isLoading) {
        button.disabled = isLoading;
        button.classList.toggle('is-loading', isLoading);
    }
})();
