// 公共工具函数
const Utils = {
    // 显示消息提示
    showMessage: function(message, type = 'info') {
        // 创建消息元素
        const messageEl = document.createElement('div');
        messageEl.className = `message message-${type}`;
        messageEl.innerHTML = `
            <div class="message-content">${message}</div>
            <button class="message-close">×</button>
        `;

        // 添加到页面
        document.body.appendChild(messageEl);

        // 添加样式（如果尚未添加）
        if (!document.querySelector('#message-styles')) {
            const styleEl = document.createElement('style');
            styleEl.id = 'message-styles';
            styleEl.textContent = `
                .message {
                    position: fixed;
                    top: 20px;
                    right: 20px;
                    padding: 15px 20px;
                    border-radius: 6px;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
                    z-index: 1000;
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    min-width: 300px;
                    max-width: 500px;
                    animation: slideIn 0.3s ease;
                    color: white;
                }
                
                .message-info {
                    background: #4a9eff;
                }
                
                .message-success {
                    background: #10b981;
                }
                
                .message-warning {
                    background: #f59e0b;
                }
                
                .message-error {
                    background: #ef4444;
                }
                
                .message-content {
                    flex: 1;
                }
                
                .message-close {
                    background: none;
                    border: none;
                    color: white;
                    font-size: 20px;
                    cursor: pointer;
                    margin-left: 15px;
                }
                
                @keyframes slideIn {
                    from {
                        transform: translateX(100%);
                        opacity: 0;
                    }
                    to {
                        transform: translateX(0);
                        opacity: 1;
                    }
                }
                
                @keyframes slideOut {
                    from {
                        transform: translateX(0);
                        opacity: 1;
                    }
                    to {
                        transform: translateX(100%);
                        opacity: 0;
                    }
                }
            `;
            document.head.appendChild(styleEl);
        }

        // 自动消失
        setTimeout(() => {
            messageEl.style.animation = 'slideOut 0.3s ease forwards';
            setTimeout(() => {
                if (messageEl.parentNode) {
                    messageEl.parentNode.removeChild(messageEl);
                }
            }, 300);
        }, 3000);

        // 点击关闭
        messageEl.querySelector('.message-close').addEventListener('click', () => {
            messageEl.style.animation = 'slideOut 0.3s ease forwards';
            setTimeout(() => {
                if (messageEl.parentNode) {
                    messageEl.parentNode.removeChild(messageEl);
                }
            }, 300);
        });
    },

    getToken() {
        // 改为从 localStorage 获取
        return localStorage.getItem('token') || sessionStorage.getItem('token');
    },

    // 在 Utils 对象中添加文件大小格式化函数
    formatFileSize: function(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    },

    // 格式化日期
    formatDate: function(date, format = 'YYYY-MM-DD') {
        const d = new Date(date);
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

    checkLogin: function() {
        // 优先从 localStorage 获取
        let token = localStorage.getItem('token');
        let refreshToken = localStorage.getItem('refresh_token');
        let userStr = localStorage.getItem('user');

        // 如果 localStorage 没有，尝试从 sessionStorage 获取（兼容旧版本）
        if (!token) {
            token = sessionStorage.getItem('token');
            refreshToken = sessionStorage.getItem('refresh_token');
            userStr = sessionStorage.getItem('user');

            // 如果 sessionStorage 有但 localStorage 没有，迁移到 localStorage
            if (token) {
                localStorage.setItem('token', token);
                if (refreshToken) localStorage.setItem('refresh_token', refreshToken);
                if (userStr) localStorage.setItem('user', userStr);
                console.log('Token 已从 sessionStorage 迁移到 localStorage');
            }
        }

        // 如果都没有 token，跳转到登录页
        if (!token && !refreshToken) {
            window.location.href = 'index.html';
            return null;
        }

        try {
            return userStr ? JSON.parse(userStr) : null;
        } catch (e) {
            console.error('解析用户信息失败:', e);
            window.location.href = 'index.html';
            return null;
        }
    },

    // 加载用户信息到侧边栏 - 简化版
    loadUserInfo: function() {
        try {
            // 优先从 localStorage 获取用户信息
            let userStr = localStorage.getItem('user');

            // 如果 localStorage 没有，尝试从 sessionStorage 获取
            if (!userStr) {
                userStr = sessionStorage.getItem('user');
                if (userStr) {
                    // 迁移到 localStorage
                    localStorage.setItem('user', userStr);
                    console.log('用户信息已从 sessionStorage 迁移到 localStorage');
                }
            }

            if (!userStr) {
                console.warn('用户信息不存在');
                return;
            }

            const user = JSON.parse(userStr);
            console.log('加载用户信息:', user);

            const avatar = document.getElementById('userAvatar');
            const name = document.getElementById('userName');
            const role = document.getElementById('userRole');

            // 设置头像
            if (avatar) {
                const displayName = user.full_name || user.username || '用户';
                avatar.textContent = displayName.charAt(0).toUpperCase();

                // 根据身份设置不同颜色
                const isAdmin = user.role === 0; // 注意：0是管理员
                avatar.style.backgroundColor = isAdmin ? '#dc2626' : '#4a9eff';
            }

            // 设置用户名
            if (name) {
                name.textContent = user.full_name || user.username || '用户';
            }

            // 设置角色显示
            if (role) {
                const roleMap = {
                    0: '系统管理员',
                    1: '维修工程师'
                };
                role.textContent = roleMap[user.role] || '用户';
            }

        } catch (error) {
            console.error('加载用户信息失败:', error);
        }
    },

    getCurrentUser: function() {
        try {
            // 优先从 localStorage 获取
            let userStr = localStorage.getItem('user');
            if (!userStr) {
                userStr = sessionStorage.getItem('user');
            }

            const user = userStr ? JSON.parse(userStr) : null;
            console.log('获取当前用户信息:', user);
            return user;
        } catch (error) {
            console.error('解析用户信息失败:', error);
            return null;
        }
    },

    // 根据身份更新菜单
    updateMenuByRole: function(user) {
        console.log('更新菜单，用户身份:', user.role, typeof user.role, user);

        // 修正角色判断逻辑 - 更严格的判断
        let isAdmin = false;

        // 检查数字类型 (0: 管理员, 1: 技术人员)
        if (typeof user.role === 'number') {
            isAdmin = user.role === 0;
        }
        // 检查字符串类型
        else if (typeof user.role === 'string') {
            isAdmin = user.role === 'admin' || user.role === '0';
        }
        // 检查 role_id
        else if (user.role_id !== undefined) {
            isAdmin = user.role_id === 0;
        }

        console.log('是管理员吗?', isAdmin);

        const userManagementLink = document.querySelector('a[href="user-management.html"]');
        const myProfileLink = document.querySelector('a[href="user-profile.html"]');
        const aiAssistLink = document.querySelector('a[href="ai-assist.html"]');

        if (userManagementLink) {
            userManagementLink.style.display = isAdmin ? 'flex' : 'none';
        }

        // 我的资料所有用户都可见
        if (myProfileLink) {
            myProfileLink.style.display = 'flex';
        }

        // 确保 AI 辅助对所有用户可见
        if (aiAssistLink) {
            aiAssistLink.style.display = 'flex';
        }

        // 移除多余的 active 类
        const navItems = document.querySelectorAll('.nav-item');
        navItems.forEach(item => {
            item.classList.remove('active');
        });

        // 根据当前页面设置 active 类
        const currentPath = window.location.pathname.split('/').pop();
        const currentLink = document.querySelector(`a[href="${currentPath}"]`);
        if (currentLink) {
            currentLink.classList.add('active');
        }
    },

    logout: function() {
        // 清除 localStorage
        localStorage.removeItem('token');
        localStorage.removeItem('refresh_token');
        localStorage.removeItem('user');
        // 清除 sessionStorage（为了兼容旧代码）
        sessionStorage.removeItem('token');
        sessionStorage.removeItem('refresh_token');
        sessionStorage.removeItem('user');
        sessionStorage.removeItem('last_conversation_id');
        // 跳转到登录页
        window.location.href = 'index.html';
    },

    // 防抖函数
    debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    },

        // 获取API基础URL
    getApiBaseUrl: function() {
        // 可以根据环境配置不同的URL
        return 'http://localhost:8000';
    },

    // 获取认证头部
    getAuthHeaders: function() {
        const token = sessionStorage.getItem('token');
        const headers = {
            'Content-Type': 'application/json',
        };

        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        return headers;
    },

    apiRequest: async function(url, options = {}) {
        try {
            const defaultOptions = {
                headers: this.getAuthHeaders(),
                ...options
            };

            // 如果有 body，确保它是 JSON 字符串
            if (defaultOptions.body && typeof defaultOptions.body !== 'string') {
                defaultOptions.body = JSON.stringify(defaultOptions.body);
            }

            const response = await fetch(url, defaultOptions);

            // 检查是否未授权
            if (response.status === 401) {
                console.log('Token过期，尝试刷新...');

                // 尝试刷新token
                try {
                    const newToken = await this.refreshToken();

                    // 更新请求头中的token
                    defaultOptions.headers['Authorization'] = `Bearer ${newToken}`;

                    // 重新发送请求
                    const retryResponse = await fetch(url, defaultOptions);

                    if (!retryResponse.ok) {
                        const errorText = await retryResponse.text();
                        throw new Error(`HTTP错误: ${retryResponse.status} - ${errorText}`);
                    }

                    const result = await retryResponse.json();

                    // 根据你的Result格式，code为1表示成功
                    if (result.code === 1) {
                        return result.data;
                    } else {
                        throw new Error(result.msg || '请求失败');
                    }
                } catch (refreshError) {
                    console.error('刷新token失败:', refreshError);
                    sessionStorage.removeItem('token');
                    sessionStorage.removeItem('refresh_token');
                    sessionStorage.removeItem('user');
                    window.location.href = 'index.html';
                    throw new Error('登录已过期，请重新登录');
                }
            }

            // 检查是否是 404
            if (response.status === 404) {
                throw new Error('请求的接口不存在，请检查路由');
            }

            // 检查其他错误状态
            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`HTTP错误: ${response.status} - ${errorText}`);
            }

            const result = await response.json();

            // 根据你的Result格式，code为1表示成功
            if (result.code === 1) {
                return result.data;
            } else {
                throw new Error(result.msg || '请求失败');
            }

        } catch (error) {
            console.error('API请求失败:', error);
            throw error;
        }
    },

    // 添加刷新token的辅助函数
    refreshToken: async function() {
        try {
            const refreshToken = sessionStorage.getItem('refresh_token');
            if (!refreshToken) {
                throw new Error('没有可用的刷新令牌');
            }

            const response = await fetch(`${this.getApiBaseUrl()}/auth/refresh`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    refresh_token: refreshToken
                })
            });

            if (!response.ok) {
                throw new Error(`刷新令牌失败: ${response.status}`);
            }

            const result = await response.json();

            if (result.code === 1) {
                const newAccessToken = result.data.access_token;
                sessionStorage.setItem('token', newAccessToken);
                console.log('Token刷新成功');
                return newAccessToken;
            } else {
                throw new Error(result.msg || '刷新令牌失败');
            }
        } catch (error) {
            console.error('刷新令牌失败:', error);
            throw error;
        }
    },
        // 新增：从 sessionStorage 迁移到 localStorage 的辅助函数
    migrateToLocalStorage: function() {
        // 检查并迁移 token
        const sessionToken = sessionStorage.getItem('token');
        const localToken = localStorage.getItem('token');

        if (sessionToken && !localToken) {
            localStorage.setItem('token', sessionToken);
            console.log('Token 已从 sessionStorage 迁移到 localStorage');
        }

        // 检查并迁移 refresh_token
        const sessionRefreshToken = sessionStorage.getItem('refresh_token');
        const localRefreshToken = localStorage.getItem('refresh_token');

        if (sessionRefreshToken && !localRefreshToken) {
            localStorage.setItem('refresh_token', sessionRefreshToken);
            console.log('Refresh token 已从 sessionStorage 迁移到 localStorage');
        }

        // 检查并迁移用户信息
        const sessionUser = sessionStorage.getItem('user');
        const localUser = localStorage.getItem('user');

        if (sessionUser && !localUser) {
            localStorage.setItem('user', sessionUser);
            console.log('用户信息已从 sessionStorage 迁移到 localStorage');
        }
    },

    // 添加获取用户信息函数（示例）
    getUserInfo: async function() {
        try {
            // 在实际项目中，调用获取用户信息的API
            // const result = await this.apiRequest('/auth/profile');
            // return result.data;

            // 临时返回sessionStorage中的用户信息
            return JSON.parse(sessionStorage.getItem('user') || '{}');
        } catch (error) {
            console.error('获取用户信息失败:', error);
            return null;
        }
    },

    // 节流函数
    throttle: function(func, limit) {
        let inThrottle;
        return function() {
            const args = arguments;
            const context = this;
            if (!inThrottle) {
                func.apply(context, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        };
    },

    // 在 common.js 文件的 Utils 对象中添加以下函数

    // 格式化消息内容（处理换行等）
    formatMessageContent: function(content) {
        return content.replace(/\n/g, '<br>');
    },

    // 生成随机ID
    generateId: function() {
        return Date.now().toString(36) + Math.random().toString(36).substr(2);
    },

    // 截断字符串
    truncateString: function(str, length) {
        if (str.length <= length) return str;
        return str.substring(0, length) + '...';
    },

    // 获取当前时间字符串
    getCurrentTime: function() {
        const now = new Date();
        return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
    }

};

// 页面加载时检查登录状态
document.addEventListener('DOMContentLoaded', function() {
    // 如果不是登录页，检查登录状态
    const currentPath = window.location.pathname;
    const isLoginPage = currentPath.includes('index.html') || currentPath === '/';

    if (!isLoginPage) {
        // 先迁移数据（如果有）
        Utils.migrateToLocalStorage();

        // 然后检查登录
        const user = Utils.checkLogin();

        // 然后加载用户信息
        if (user) {
            // 延迟一点确保DOM完全加载
            setTimeout(() => {
                Utils.loadUserInfo();
            }, 100);
        }
    }
});

// 表单验证
function initFormValidation() {
    const forms = document.querySelectorAll('form[data-validate]');
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            let isValid = true;
            const inputs = this.querySelectorAll('[required]');

            inputs.forEach(input => {
                if (!input.value.trim()) {
                    isValid = false;
                    highlightError(input, '此字段不能为空');
                } else {
                    clearError(input);
                }

                // 邮箱验证
                if (input.type === 'email' && input.value) {
                    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
                    if (!emailRegex.test(input.value)) {
                        isValid = false;
                        highlightError(input, '请输入有效的邮箱地址');
                    }
                }

                // 密码长度验证
                if (input.type === 'password' && input.value) {
                    if (input.value.length < 6) {
                        isValid = false;
                        highlightError(input, '密码长度不能少于6位');
                    }
                }
            });

            if (!isValid) {
                e.preventDefault();
                Utils.showMessage('请检查表单中的错误', 'error');
            }
        });
    });
}

// 高亮错误字段
function highlightError(input, message) {
    const formGroup = input.closest('.form-group');
    if (!formGroup) return;

    // 移除现有的错误信息
    const existingError = formGroup.querySelector('.error-message');
    if (existingError) existingError.remove();

    // 添加错误样式
    input.classList.add('error');

    // 添加错误信息
    const errorEl = document.createElement('div');
    errorEl.className = 'error-message';
    errorEl.textContent = message;
    errorEl.style.color = '#ef4444';
    errorEl.style.fontSize = '12px';
    errorEl.style.marginTop = '4px';

    formGroup.appendChild(errorEl);
}

// 清除错误提示
function clearError(input) {
    input.classList.remove('error');
    const formGroup = input.closest('.form-group');
    if (!formGroup) return;

    const errorMessage = formGroup.querySelector('.error-message');
    if (errorMessage) errorMessage.remove();
}

// 图片上传处理
function initImageUpload(uploadAreaId, previewContainerId) {
    const uploadArea = document.getElementById(uploadAreaId);
    const previewContainer = document.getElementById(previewContainerId);
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.multiple = true;
    fileInput.accept = 'image/*';
    fileInput.style.display = 'none';

    document.body.appendChild(fileInput);

    // 点击上传区域选择文件
    uploadArea.addEventListener('click', () => {
        fileInput.click();
    });

    // 拖放上传
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

        const files = e.dataTransfer.files;
        handleImageFiles(files, previewContainer);
    });

    // 文件选择变化
    fileInput.addEventListener('change', () => {
        handleImageFiles(fileInput.files, previewContainer);
    });

    return {
        getSelectedFiles: () => fileInput.files
    };
}

// 处理图片文件
function handleImageFiles(files, previewContainer) {
    previewContainer.innerHTML = '';

    for (let file of files) {
        if (!file.type.startsWith('image/')) continue;

        const reader = new FileReader();
        reader.onload = function(e) {
            const preview = document.createElement('div');
            preview.className = 'image-preview';
            preview.innerHTML = `
                <img src="${e.target.result}" alt="预览">
                <button type="button" class="btn-remove-image">×</button>
            `;

            preview.querySelector('.btn-remove-image').addEventListener('click', () => {
                preview.remove();
            });

            previewContainer.appendChild(preview);
        };
        reader.readAsDataURL(file);
    }
}


// 导出到全局
window.Utils = Utils;
window.initImageUpload = initImageUpload;

// // 在文件顶部添加分页变量
// let currentPage = 1;
// let pageSize = 6; // 默认每页显示4人
// let totalPages = 1;

// // 修改 loadUsers 函数
// function loadUsers() {
//     const users = MockData.getUsers();
//     totalPages = Math.ceil(users.length / pageSize);
//     displayUsers(users);
//     renderPagination(users.length);
// }
//
// // 修改 displayUsers 函数，添加分页逻辑
// function displayUsers(users) {
//     const container = document.getElementById('userList');
//     const countElement = document.getElementById('userCount');
//
//     if (users.length === 0) {
//         container.innerHTML = `
//             <div style="text-align: center; padding: 60px; color: #666; width: 100%;" class="empty-state">
//                 <i class="fas fa-users" style="font-size: 56px; margin-bottom: 20px; color: #e8e8e8;"></i>
//                 <p>暂无用户数据</p>
//                 <button class="btn btn-primary" onclick="showAddUserModal()" style="margin-top: 15px;">
//                     <i class="fas fa-user-plus"></i> 添加第一个用户
//                 </button>
//             </div>
//         `;
//         countElement.textContent = '共0个用户';
//         return;
//     }
//
//     // 分页处理
//     const startIndex = (currentPage - 1) * pageSize;
//     const endIndex = startIndex + pageSize;
//     const pageUsers = users.slice(startIndex, endIndex);
//
//     let html = '';
//     pageUsers.forEach(user => {
//         const colors = ['#4a9eff', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];
//         const colorIndex = user.id % colors.length;
//         const avatarColor = user.avatarColor || colors[colorIndex];
//
//         html += `
//             <div class="user-card">
//                 <div class="user-avatar-large" style="background: ${avatarColor}">
//                     ${user.name.charAt(0)}
//                 </div>
//                 <div class="user-info-details">
//                     <div class="user-info-name">${user.name}</div>
//                     <div class="user-info-role">
//                         <span class="badge" style="background: ${user.role === 'admin' ? '#fee2e2' : '#e7f5ff'};
//                             color: ${user.role === 'admin' ? '#dc2626' : '#1971c2'};
//                             padding: 4px 12px; border-radius: 16px; font-size: 13px; font-weight: 500;">
//                             ${user.role === 'admin' ? '👑 系统管理员' : '👷 技术人员'}
//                         </span>
//                     </div>
//                     <ul class="user-info-list">
//                         <li><i class="fas fa-building"></i> 部门：${user.department}</li>
//                         <li><i class="fas fa-phone"></i> 电话：${user.phone}</li>
//                         <li><i class="fas fa-envelope"></i> 邮箱：${user.email}</li>
//                         <li><i class="fas fa-calendar-alt"></i> 入职时间：${user.hireDate}</li>
//                     </ul>
//                     <div style="margin-top: 20px; display: flex; gap: 12px;">
//                         <button class="btn btn-secondary btn-sm" onclick="editUser(${user.id})">
//                             <i class="fas fa-edit"></i> 编辑
//                         </button>
//                         <button class="btn btn-danger btn-sm" onclick="deleteUser(${user.id})" ${user.role === 'admin' ? 'disabled' : ''}>
//                             <i class="fas fa-trash"></i> 删除
//                         </button>
//                     </div>
//                 </div>
//             </div>
//         `;
//     });
//
//     container.innerHTML = html;
//     countElement.textContent = `共${users.length}个用户，当前显示第 ${startIndex + 1}-${Math.min(endIndex, users.length)} 个`;
// }
//
// // 渲染分页控件
// function renderPagination(totalUsers) {
//     const pageNumbersContainer = document.getElementById('pageNumbers');
//     const prevBtn = document.getElementById('prevBtn');
//     const nextBtn = document.getElementById('nextBtn');
//     const pageInfo = document.getElementById('pageInfo');
//
//     totalPages = Math.ceil(totalUsers / pageSize);
//
//     // 更新页面信息
//     pageInfo.textContent = `第 ${currentPage} 页，共 ${totalPages} 页`;
//
//     // 更新按钮状态
//     prevBtn.disabled = currentPage <= 1;
//     nextBtn.disabled = currentPage >= totalPages;
//
//     // 生成页码
//     let pageNumbersHtml = '';
//
//     // 总是显示第一页
//     pageNumbersHtml += `
//         <button class="page-number ${currentPage === 1 ? 'active' : ''}" onclick="changePage(1)">
//             1
//         </button>
//     `;
//
//     // 显示省略号（如果当前页离第一页较远）
//     if (currentPage > 3) {
//         pageNumbersHtml += `<span class="page-dots">...</span>`;
//     }
//
//     // 显示当前页附近的页码
//     for (let i = Math.max(2, currentPage - 1); i <= Math.min(totalPages - 1, currentPage + 1); i++) {
//         if (i > 1 && i < totalPages) {
//             pageNumbersHtml += `
//                 <button class="page-number ${currentPage === i ? 'active' : ''}" onclick="changePage(${i})">
//                     ${i}
//                 </button>
//             `;
//         }
//     }
//
//     // 显示省略号（如果当前页离最后一页较远）
//     if (currentPage < totalPages - 2) {
//         pageNumbersHtml += `<span class="page-dots">...</span>`;
//     }
//
//     // 总是显示最后一页（如果有超过一页）
//     if (totalPages > 1) {
//         pageNumbersHtml += `
//             <button class="page-number ${currentPage === totalPages ? 'active' : ''}" onclick="changePage(${totalPages})">
//                 ${totalPages}
//             </button>
//         `;
//     }
//
//     pageNumbersContainer.innerHTML = pageNumbersHtml;
// }
//
// // 切换页码
// function changePage(page) {
//     if (page < 1 || page > totalPages) return;
//
//     currentPage = page;
//     const users = MockData.getUsers();
//     displayUsers(users);
//     renderPagination(users.length);
//
//     // 滚动到顶部
//     document.querySelector('.user-list').scrollIntoView({ behavior: 'smooth' });
// }
//
// // 更改每页显示数量
// function changePageSize() {
//     const select = document.getElementById('pageSizeSelect');
//     pageSize = parseInt(select.value);
//     currentPage = 1; // 重置到第一页
//     loadUsers();
// }
//
// // 修改添加用户函数，成功后重置到第一页
// function addUser() {
//     // ... 原有的验证和添加逻辑保持不变 ...
//
//     // 在用户添加成功后，重置分页
//     const addedUser = MockData.addUser(newUser);
//     Utils.showMessage(`用户"${name}"添加成功！`, 'success');
//
//     closeAddUserModal();
//
//     // 重置到第一页并重新加载
//     currentPage = 1;
//     loadUsers();
// }
//
// // 修改删除用户函数，成功后重新计算分页
// function deleteUser(userId) {
//     if (!confirm('确定要删除这个用户吗？此操作不可恢复。')) {
//         return;
//     }
//
//     const success = MockData.deleteUser(userId);
//     if (success) {
//         Utils.showMessage('用户删除成功', 'success');
//
//         // 重新加载数据，分页会自动调整
//         loadUsers();
//
//         // 如果当前页没有数据了，自动回到前一页
//         const users = MockData.getUsers();
//         const startIndex = (currentPage - 1) * pageSize;
//         if (users.length <= startIndex && currentPage > 1) {
//             currentPage--;
//             loadUsers();
//         }
//     } else {
//         Utils.showMessage('删除用户失败', 'error');
//     }
// }
//
// // 在 window 对象上导出分页函数
// window.changePage = changePage;
// window.changePageSize = changePageSize;
