(function () {
    'use strict';

    const LOGIN_PAGE = 'login.html';
    const AI_PAGE = 'ai.html';

    const MobileProfileUtils = {
        toastTimer: null,

        checkLogin() {
            const token = localStorage.getItem('token') || sessionStorage.getItem('token');
            const refreshToken = localStorage.getItem('refresh_token') || sessionStorage.getItem('refresh_token');
            const userStr = localStorage.getItem('user') || sessionStorage.getItem('user');

            if (!token && !refreshToken) {
                window.location.replace(LOGIN_PAGE);
                return null;
            }

            if (token) {
                localStorage.setItem('token', token);
                sessionStorage.setItem('token', token);
            }
            if (refreshToken) {
                localStorage.setItem('refresh_token', refreshToken);
                sessionStorage.setItem('refresh_token', refreshToken);
            }
            if (userStr) {
                localStorage.setItem('user', userStr);
                sessionStorage.setItem('user', userStr);
            }

            try {
                return userStr ? JSON.parse(userStr) : null;
            } catch (_) {
                this.logout();
                return null;
            }
        },

        logout() {
            ['token', 'refresh_token', 'user'].forEach((key) => {
                localStorage.removeItem(key);
                sessionStorage.removeItem(key);
            });
            sessionStorage.removeItem('last_conversation_id');
            window.location.href = LOGIN_PAGE;
        },

        showMessage(message, type = 'info') {
            let toast = document.querySelector('.mobile-toast');
            if (!toast) {
                toast = document.createElement('div');
                toast.className = 'mobile-toast';
                toast.setAttribute('role', 'status');
                toast.setAttribute('aria-live', 'polite');
                document.body.appendChild(toast);
            }
            toast.className = `mobile-toast mobile-toast-${type}`;
            toast.textContent = message;
            requestAnimationFrame(() => toast.classList.add('is-visible'));

            if (this.toastTimer) window.clearTimeout(this.toastTimer);
            this.toastTimer = window.setTimeout(() => {
                toast.classList.remove('is-visible');
            }, 2800);
        },

        formatDate(dateString) {
            if (!dateString) return '未设置';
            const date = new Date(dateString);
            if (Number.isNaN(date.getTime())) return dateString;
            const pad = (value) => String(value).padStart(2, '0');
            return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
        },

        getDisplayName(user) {
            return (user && (user.full_name || user.username)) || '未设置';
        },

        getRoleText(user) {
            if (!user) return '用户';
            if (user.role_group_name) return user.role_group_name;
            if (user.role_name) return user.role_name;
            const role = Number(user.role ?? user.role_id);
            const map = {
                0: '系统管理员',
                1: '技术人员',
                2: '审核人员',
                3: '维修人员'
            };
            return map[role] || '用户';
        },

        isAdmin(user) {
            if (!user) return false;
            if (Array.isArray(user.permissions) && user.permissions.includes('admin')) return true;
            return Number(user.role ?? user.role_id) === 0;
        }
    };

    function patchApiRedirects() {
        if (typeof APIClient === 'undefined' || !APIClient.prototype) return;
        APIClient.prototype.refreshToken = async function refreshTokenForMobile() {
            const refreshToken = localStorage.getItem('refresh_token') || sessionStorage.getItem('refresh_token');
            if (!refreshToken) {
                MobileProfileUtils.logout();
                throw new Error('没有可用的刷新令牌');
            }

            const response = await fetch(`${this.baseUrl}/auth/refresh`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: refreshToken })
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok || Number(result.code) !== 1) {
                MobileProfileUtils.logout();
                throw new Error(result.msg || result.message || '登录已过期，请重新登录');
            }

            const tokenData = result.data || {};
            if (tokenData.access_token) {
                localStorage.setItem('token', tokenData.access_token);
                sessionStorage.setItem('token', tokenData.access_token);
            }
            if (tokenData.refresh_token) {
                localStorage.setItem('refresh_token', tokenData.refresh_token);
                sessionStorage.setItem('refresh_token', tokenData.refresh_token);
            }
            return tokenData.access_token;
        };
    }

    class MobileProfilePage {
        constructor() {
            this.currentUser = null;
            this.passwordStrengthTimer = null;
        }

        async init() {
            patchApiRedirects();
            MobileProfileUtils.checkLogin();
            this.bindEvents();
            await this.loadUserProfile();
        }

        bindEvents() {
            const bindClick = (id, fn) => {
                const el = document.getElementById(id);
                if (el) el.addEventListener('click', fn);
            };

            bindClick('topLogoutBtn', () => this.confirmLogout());
            bindClick('logoutBtn', () => this.confirmLogout());
            bindClick('editProfileBtn', () => this.switchToEditMode());
            bindClick('cancelEditTopBtn', () => this.switchToViewMode());
            bindClick('cancelEditBtn', () => this.switchToViewMode());
            bindClick('showPasswordModalBtn', () => this.openPasswordModal());
            bindClick('closePasswordModalBtn', () => this.closePasswordModal());
            bindClick('cancelPasswordBtn', () => this.closePasswordModal());
            bindClick('passwordModalOverlay', () => this.closePasswordModal());

            const profileForm = document.getElementById('profileForm');
            if (profileForm) {
                profileForm.addEventListener('submit', (event) => {
                    event.preventDefault();
                    this.saveProfile();
                });
            }

            const passwordForm = document.getElementById('changePasswordForm');
            if (passwordForm) {
                passwordForm.addEventListener('submit', (event) => {
                    event.preventDefault();
                    this.changePassword();
                });
            }

            const newPassword = document.getElementById('newPassword');
            if (newPassword) {
                newPassword.addEventListener('input', () => {
                    window.clearTimeout(this.passwordStrengthTimer);
                    this.passwordStrengthTimer = window.setTimeout(() => this.updatePasswordStrength(), 120);
                });
            }

            document.querySelectorAll('.password-toggle').forEach((button) => {
                button.addEventListener('click', () => this.togglePasswordVisibility(button));
            });
        }

        async loadUserProfile() {
            this.showLoading();
            try {
                if (typeof userAPI === 'undefined') throw new Error('用户接口未加载');
                const response = await userAPI.getUserProfile();
                if (!response || Number(response.code) !== 1 || !response.data) {
                    throw new Error(response?.msg || response?.message || '获取用户信息失败');
                }
                this.currentUser = response.data;
                this.persistUser(this.currentUser);
                this.renderProfile(this.currentUser);
                this.updateEditForm(this.currentUser);
            } catch (error) {
                this.showError(`加载用户信息失败：${error.message}`);
            }
        }

        persistUser(user) {
            const userJson = JSON.stringify(user);
            localStorage.setItem('user', userJson);
            sessionStorage.setItem('user', userJson);
        }

        showLoading() {
            const hero = document.getElementById('profileHero');
            const list = document.getElementById('profileInfoList');
            if (hero) {
                hero.innerHTML = '<div class="profile-loading"><span class="loading-dot"></span><span>正在加载用户信息...</span></div>';
            }
            if (list) list.innerHTML = '';
        }

        showError(message) {
            const hero = document.getElementById('profileHero');
            if (hero) {
                hero.innerHTML = `
                    <div class="profile-error">
                        <div class="hero-avatar error-avatar"><i class="fas fa-exclamation-triangle"></i></div>
                        <div class="hero-info">
                            <h2>加载失败</h2>
                            <p>${this.escapeHtml(message)}</p>
                            <button class="small-primary-btn" id="reloadProfileBtn" type="button"><i class="fas fa-redo"></i> 重新加载</button>
                        </div>
                    </div>`;
                document.getElementById('reloadProfileBtn')?.addEventListener('click', () => this.loadUserProfile());
            }
            MobileProfileUtils.showMessage(message, 'error');
        }

        renderProfile(user) {
            const hero = document.getElementById('profileHero');
            const list = document.getElementById('profileInfoList');
            const name = MobileProfileUtils.getDisplayName(user);
            const avatarChar = name.charAt(0).toUpperCase();
            const roleText = MobileProfileUtils.getRoleText(user);
            const avatarBg = MobileProfileUtils.isAdmin(user)
                ? 'linear-gradient(135deg, #dc2626 0%, #b91c1c 100%)'
                : 'linear-gradient(135deg, var(--mobile-primary), var(--mobile-primary-dark))';

            if (hero) {
                hero.innerHTML = `
                    <div class="hero-user">
                        <div class="hero-avatar" style="background: ${avatarBg}">${this.escapeHtml(avatarChar)}</div>
                        <div class="hero-info">
                            <h2>${this.escapeHtml(name)}</h2>
                            <p>${this.escapeHtml(user.username || '未设置')}</p>
                            <span class="role-pill"><i class="fas fa-id-badge"></i>${this.escapeHtml(roleText)}</span>
                        </div>
                    </div>`;
            }

            const fields = [
                { icon: 'fa-user', label: '用户名', value: user.username || '未设置' },
                { icon: 'fa-building', label: '部门', value: user.department || '未设置' },
                { icon: 'fa-phone', label: '电话', value: user.phone || '未设置' },
                { icon: 'fa-envelope', label: '邮箱', value: user.email || '未设置' },
                { icon: 'fa-calendar-alt', label: '注册时间', value: MobileProfileUtils.formatDate(user.created_time) },
                { icon: 'fa-clock', label: '最后登录', value: user.last_login ? MobileProfileUtils.formatDate(user.last_login) : '从未登录' }
            ];

            if (list) {
                list.innerHTML = fields.map((field) => `
                    <div class="info-item">
                        <span class="info-icon"><i class="fas ${field.icon}"></i></span>
                        <div>
                            <div class="info-label">${this.escapeHtml(field.label)}</div>
                            <div class="info-value">${this.escapeHtml(field.value)}</div>
                        </div>
                    </div>`).join('');
            }
        }

        updateEditForm(user) {
            this.setValue('editFullName', user.full_name || '');
            this.setValue('editPhone', user.phone || '');
            this.setValue('editEmail', user.email || '');
            this.setValue('editDepartment', user.department || '');
        }

        switchToEditMode() {
            const view = document.getElementById('profileViewCard');
            const edit = document.getElementById('profileEditCard');
            if (this.currentUser) this.updateEditForm(this.currentUser);
            if (view) view.hidden = true;
            if (edit) edit.hidden = false;
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        switchToViewMode() {
            const view = document.getElementById('profileViewCard');
            const edit = document.getElementById('profileEditCard');
            if (view) view.hidden = false;
            if (edit) edit.hidden = true;
        }

        async saveProfile() {
            const fullName = this.getValue('editFullName');
            const phone = this.getValue('editPhone');
            const email = this.getValue('editEmail');
            const department = this.getValue('editDepartment');

            if (!fullName) {
                MobileProfileUtils.showMessage('姓名不能为空', 'error');
                this.focus('editFullName');
                return;
            }
            if (!phone) {
                MobileProfileUtils.showMessage('电话不能为空', 'error');
                this.focus('editPhone');
                return;
            }
            if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
                MobileProfileUtils.showMessage('请输入有效的邮箱地址', 'error');
                this.focus('editEmail');
                return;
            }
            if (!/^1[3-9]\d{9}$/.test(phone)) {
                MobileProfileUtils.showMessage('请输入有效的手机号码', 'error');
                this.focus('editPhone');
                return;
            }

            this.setButtonLoading('saveProfileBtn', true);
            try {
                const response = await userAPI.updateUser({
                    full_name: fullName,
                    phone,
                    email: email || null,
                    department: department || null
                });
                if (!response || Number(response.code) !== 1) {
                    throw new Error(response?.msg || response?.message || '更新失败');
                }
                this.currentUser = response.data || {
                    ...this.currentUser,
                    full_name: fullName,
                    phone,
                    email: email || null,
                    department: department || null
                };
                this.persistUser(this.currentUser);
                this.renderProfile(this.currentUser);
                this.switchToViewMode();
                MobileProfileUtils.showMessage('个人信息更新成功', 'success');
            } catch (error) {
                MobileProfileUtils.showMessage(`保存失败：${error.message}`, 'error');
            } finally {
                this.setButtonLoading('saveProfileBtn', false);
            }
        }

        openPasswordModal() {
            const overlay = document.getElementById('passwordModalOverlay');
            const modal = document.getElementById('passwordModal');
            const form = document.getElementById('changePasswordForm');
            if (form) form.reset();
            this.updatePasswordStrength();
            if (overlay) overlay.hidden = false;
            if (modal) {
                modal.setAttribute('aria-hidden', 'false');
                requestAnimationFrame(() => modal.classList.add('is-open'));
            }
            window.setTimeout(() => this.focus('currentPassword'), 260);
        }

        closePasswordModal() {
            const overlay = document.getElementById('passwordModalOverlay');
            const modal = document.getElementById('passwordModal');
            if (modal) {
                modal.classList.remove('is-open');
                modal.setAttribute('aria-hidden', 'true');
            }
            window.setTimeout(() => {
                if (overlay) overlay.hidden = true;
            }, 240);
        }

        async changePassword() {
            const currentPassword = this.getValue('currentPassword');
            const newPassword = this.getValue('newPassword');
            const confirmPassword = this.getValue('confirmPassword');

            if (!currentPassword || !newPassword || !confirmPassword) {
                MobileProfileUtils.showMessage('请填写完整的密码信息', 'error');
                return;
            }
            if (newPassword !== confirmPassword) {
                MobileProfileUtils.showMessage('两次输入的新密码不一致', 'error');
                return;
            }
            if (newPassword.length < 6) {
                MobileProfileUtils.showMessage('密码长度不能少于6位', 'error');
                return;
            }

            this.setButtonLoading('changePasswordBtn', true);
            try {
                const response = await userAPI.changePassword(currentPassword, newPassword);
                if (!response || Number(response.code) !== 1) {
                    throw new Error(response?.msg || response?.message || '修改密码失败');
                }
                MobileProfileUtils.showMessage('密码修改成功', 'success');
                this.closePasswordModal();
                document.getElementById('changePasswordForm')?.reset();
                this.updatePasswordStrength();
            } catch (error) {
                MobileProfileUtils.showMessage(`修改密码失败：${error.message}`, 'error');
            } finally {
                this.setButtonLoading('changePasswordBtn', false);
            }
        }

        togglePasswordVisibility(button) {
            const targetId = button.getAttribute('data-target');
            const input = targetId ? document.getElementById(targetId) : null;
            const icon = button.querySelector('i');
            if (!input || !icon) return;

            const shouldShow = input.type === 'password';
            input.type = shouldShow ? 'text' : 'password';
            icon.classList.toggle('fa-eye', !shouldShow);
            icon.classList.toggle('fa-eye-slash', shouldShow);
            button.setAttribute('aria-label', shouldShow ? '隐藏密码' : '显示密码');
        }

        checkPasswordStrength(password) {
            if (!password || password.length < 6) return 1;
            if (password.length < 8) return 2;
            let strength = 2;
            if (/[A-Z]/.test(password)) strength++;
            if (/[0-9]/.test(password)) strength++;
            if (/[^A-Za-z0-9]/.test(password)) strength++;
            return Math.min(strength, 4);
        }

        updatePasswordStrength() {
            const password = this.getValue('newPassword');
            const strength = this.checkPasswordStrength(password);
            const bar = document.getElementById('passwordStrengthBar');
            const text = document.getElementById('passwordStrengthText');
            const colors = ['#ef4444', '#f59e0b', '#84cc16', '#10b981'];
            const labels = ['弱', '一般', '良好', '强'];
            const level = Math.max(0, Math.min(strength - 1, 3));
            if (bar) {
                bar.style.width = `${(level + 1) * 25}%`;
                bar.style.backgroundColor = colors[level];
            }
            if (text) {
                text.textContent = labels[level];
                text.style.color = colors[level];
            }
        }

        confirmLogout() {
            if (window.confirm('确定要退出登录吗？')) {
                MobileProfileUtils.logout();
            }
        }

        setButtonLoading(id, loading) {
            const button = document.getElementById(id);
            if (!button) return;
            button.disabled = loading;
            button.classList.toggle('is-loading', loading);
            button.setAttribute('aria-busy', String(loading));
        }

        getValue(id) {
            const el = document.getElementById(id);
            return el ? el.value.trim() : '';
        }

        setValue(id, value) {
            const el = document.getElementById(id);
            if (el) el.value = value;
        }

        focus(id) {
            document.getElementById(id)?.focus({ preventScroll: false });
        }

        escapeHtml(value) {
            const div = document.createElement('div');
            div.textContent = value === undefined || value === null ? '' : String(value);
            return div.innerHTML;
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (typeof userAPI === 'undefined') {
            MobileProfileUtils.showMessage('用户接口加载失败，请刷新页面', 'error');
            return;
        }
        window.MobileProfile = new MobileProfilePage();
        window.MobileProfile.init();

        const backButton = document.querySelector('.back-button');
        if (backButton) backButton.href = AI_PAGE;
    });
})();
