class APIClient {
    constructor() {
        this.baseUrl = API_CONFIG.BASE_URL;
        this.headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        };
    }

    // 获取认证头
    getAuthHeaders() {
        const token = sessionStorage.getItem('token');
        const headers = { ...this.headers };

        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        return headers;
    }

    // POST请求方法 - 完全移除 credentials
    async post(endpoint, data, useAuth = true) {
        try {
            const url = `${this.baseUrl}${endpoint}`;
            const options = {
                method: 'POST',
                headers: useAuth ? this.getAuthHeaders() : this.headers,
                body: JSON.stringify(data)
                // 注意：移除了 credentials: 'include' 和 mode: 'cors'
            };

            console.log(`发送 POST 请求到: ${url}`, data);

            const response = await fetch(url, options);
            console.log('响应状态:', response.status);

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || errorData.msg || `HTTP ${response.status}`);
            }

            const responseData = await response.json();
            console.log('响应数据:', responseData);
            return responseData;
        } catch (error) {
            console.error('POST请求失败:', error);
            throw error;
        }
    }

    // PATCH 请求方法 - 完全移除 credentials
    async patch(endpoint, data, useAuth = true) {
        try {
            const url = `${this.baseUrl}${endpoint}`;
            const options = {
                method: 'PATCH',
                headers: useAuth ? this.getAuthHeaders() : this.headers,
                body: JSON.stringify(data)
            };

            console.log(`发送 PATCH 请求到: ${url}`, data);

            const response = await fetch(url, options);
            console.log('响应状态:', response.status);

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || errorData.msg || `HTTP ${response.status}`);
            }

            const responseData = await response.json();
            console.log('响应数据:', responseData);
            return responseData;
        } catch (error) {
            console.error('PATCH请求失败:', error);
            throw error;
        }
    }

    // GET请求方法 - 完全移除 credentials
    async get(endpoint, useAuth = true) {
        try {
            const url = `${this.baseUrl}${endpoint}`;
            const options = {
                method: 'GET',
                headers: useAuth ? this.getAuthHeaders() : this.headers
            };

            console.log(`发送 GET 请求到: ${url}`);

            const response = await fetch(url, options);
            console.log('响应状态:', response.status);

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || errorData.msg || `HTTP ${response.status}`);
            }

            const responseData = await response.json();
            console.log('响应数据:', responseData);
            return responseData;
        } catch (error) {
            console.error('GET请求失败:', error);
            throw error;
        }
    }

    // PUT请求方法 - 完全移除 credentials
    async put(endpoint, data, useAuth = true) {
        try {
            const url = `${this.baseUrl}${endpoint}`;
            const options = {
                method: 'PUT',
                headers: useAuth ? this.getAuthHeaders() : this.headers,
                body: JSON.stringify(data)
            };

            const response = await fetch(url, options);

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || errorData.msg || `HTTP ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error('PUT请求失败:', error);
            throw error;
        }
    }

    // DELETE请求方法 - 完全移除 credentials
    async delete(endpoint, data = null, useAuth = true) {
        try {
            const url = `${this.baseUrl}${endpoint}`;
            const options = {
                method: 'DELETE',
                headers: useAuth ? this.getAuthHeaders() : this.headers
            };

            if (data) {
                options.body = JSON.stringify(data);
            }

            const response = await fetch(url, options);

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || errorData.msg || `HTTP ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error('DELETE请求失败:', error);
            throw error;
        }
    }

    // 上传多张图片
    async uploadImages(endpoint, files) {
        try {
            const url = `${this.baseUrl}${endpoint}`;
            const formData = new FormData();

            files.forEach((file, index) => {
                formData.append('images', file);
            });

            const options = {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${sessionStorage.getItem('token')}`
                },
                body: formData
            };

            const response = await fetch(url, options);

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || errorData.msg || `HTTP ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error('上传图片失败:', error);
            throw error;
        }
    }
}


const documentAPI = {
    client: new APIClient(),

    // 分页获取文档
    async getDocumentsPage(page = 1, size = 9) {
        try {
            console.log(`POST请求获取第 ${page} 页文档，每页 ${size} 条`);

            const requestData = {
                page: page,
                size: size
            };

            const response = await this.client.post(
                `${API_CONFIG.ENDPOINTS.DOCUMENTS}/page`,
                requestData,
                true
            );

            console.log('分页响应:', response);

            if (response.code === 1) {
                return response.data || {
                    total_count: 0,
                    total_pages: 0,
                    documents: []
                };
            } else {
                console.error('获取分页文档失败:', response.msg);
                return {
                    total_count: 0,
                    total_pages: 0,
                    documents: []
                };
            }
        } catch (error) {
            console.error('获取分页文档失败:', error);
            return {
                total_count: 0,
                total_pages: 0,
                documents: []
            };
        }
    },

    // 获取所有文档
    async getAllDocuments() {
        try {
            const response = await this.client.get(
                API_CONFIG.ENDPOINTS.DOCUMENTS,
                true
            );

            if (response.code === 1) {
                return response.data || [];
            } else {
                console.error('获取文档失败:', response.msg);
                return [];
            }
        } catch (error) {
            console.error('获取文档列表失败:', error);
            return [];
        }
    },

    // 根据ID获取文档
    async getDocumentById(id) {
        try {
            const response = await this.client.get(
                `${API_CONFIG.ENDPOINTS.DOCUMENTS}/get_by_id/${id}`,
                true
            );

            if (response.code === 1) {
                return response.data;
            } else {
                console.error('获取文档详情失败:', response.msg);
                return null;
            }
        } catch (error) {
            console.error('获取文档详情失败:', error);
            return null;
        }
    },

    // 添加文档
    async addDocument(documentData) {
        try {
            const response = await this.client.post(
                `${API_CONFIG.ENDPOINTS.DOCUMENTS}/add`,
                documentData,
                true
            );

            if (response.code === 1) {
                return response.data;
            } else {
                throw new Error(response.msg || '添加文档失败');
            }
        } catch (error) {
            console.error('添加文档失败:', error);
            throw error;
        }
    },

    // 上传图片
    async uploadImages(images) {
        try {
            const response = await this.client.uploadImages(
                `${API_CONFIG.ENDPOINTS.DOCUMENTS}/upload_images`,
                images
            );

            if (response.code === 1) {
                return response.data || [];
            } else {
                throw new Error(response.msg || '上传图片失败');
            }
        } catch (error) {
            console.error('上传图片失败:', error);
            throw error;
        }
    },

    // 更新文档
    async updateDocument(id, documentData) {
        try {
            const response = await this.client.put(
                `${API_CONFIG.ENDPOINTS.DOCUMENTS}/update?id=${id}`,
                documentData,
                true
            );

            if (response.code === 1) {
                return response.data;
            } else {
                throw new Error(response.msg || '更新文档失败');
            }
        } catch (error) {
            console.error('更新文档失败:', error);
            throw error;
        }
    },

    // 删除文档
    async deleteDocument(id) {
        try {
            const response = await this.client.delete(
                `${API_CONFIG.ENDPOINTS.DOCUMENTS}/dele/${id}`,
                null,
                true
            );

            if (response.code === 1) {
                return true;
            } else {
                throw new Error(response.msg || '删除文档失败');
            }
        } catch (error) {
            console.error('删除文档失败:', error);
            throw error;
        }
    },

    // 删除图片
    async deleteImage(imageUrl) {
        try {
            console.log('调用删除图片API，URL:', imageUrl);

            const response = await this.client.delete(
                `${API_CONFIG.ENDPOINTS.DOCUMENTS}/delete_image`,
                { image_url: imageUrl },
                true
            );

            console.log('删除图片响应:', response);

            if (response.code === 1) {
                return true;
            } else {
                throw new Error(response.msg || '删除图片失败');
            }
        } catch (error) {
            console.error('删除图片失败:', error);
            throw error;
        }
    },

    // 搜索文档（分页）
    async searchDocumentsPage(query, page = 1, size = 9) {
        try {
            console.log(`搜索文档: "${query}", 第 ${page} 页，每页 ${size} 条`);

            const requestData = {
                data: query,
                page: page,
                size: size
            };

            console.log('搜索文档请求数据:', requestData);

            const response = await this.client.post(
                `${API_CONFIG.ENDPOINTS.DOCUMENTS}/query`,
                requestData,
                true
            );

            console.log('搜索文档响应:', response);

            if (response.code === 1) {
                const data = response.data || {
                    total_count: 0,
                    total_pages: 0,
                    users: []
                };

                const documents = data.users || data.documents || [];

                return {
                    total_count: data.total_count || 0,
                    total_pages: data.total_pages || 0,
                    documents: documents
                };
            } else {
                console.error('搜索文档失败:', response.msg);
                return {
                    total_count: 0,
                    total_pages: 0,
                    documents: []
                };
            }
        } catch (error) {
            console.error('搜索文档失败:', error);
            return {
                total_count: 0,
                total_pages: 0,
                documents: []
            };
        }
    },

    // 检查文档编辑权限
    async checkEditPermission(id) {
        try {
            const doc = await this.getDocumentById(id);
            const currentUser = Utils.getCurrentUser();

            if (!doc || !currentUser) return false;

            const isAdmin = currentUser.role === 'admin' || currentUser.role_id === 0;
            const isAuthor = currentUser.id == doc.contributor_id;

            return isAdmin || isAuthor;
        } catch (error) {
            console.error('检查编辑权限失败:', error);
            return false;
        }
    }
};

// 用户相关的 API
const userAPI = {
    client: new APIClient(),

    // 登录
    async login(username, password, role) {
        try {
            const response = await this.client.post(
                API_CONFIG.ENDPOINTS.LOGIN,
                {
                    username,
                    password,
                    role: role === 'admin' ? 0 : 1
                },
                false
            );

            console.log('登录响应:', response);

            if (response.code === 1) {
                if (response.data && response.data.access_token) {
                    sessionStorage.setItem('token', response.data.access_token);
                    console.log('Token 已保存');
                }
                return response.data;
            } else {
                throw new Error(response.msg || '登录失败');
            }
        } catch (error) {
            console.error('登录失败:', error);
            throw error;
        }
    },

    // 获取当前用户信息
    async getCurrentUser() {
        try {
            const response = await this.client.get(
                `${API_CONFIG.ENDPOINTS.USER}/me`,
                true
            );

            if (response.code === 1) {
                return response.data;
            } else {
                throw new Error(response.msg || '获取用户信息失败');
            }
        } catch (error) {
            console.error('获取用户信息失败:', error);
            throw error;
        }
    },

    // 获取用户资料
    async getUserProfile() {
        try {
            console.log('获取用户资料，端点:', `${API_CONFIG.ENDPOINTS.USER}/profile`);

            const response = await this.client.get(
                `${API_CONFIG.ENDPOINTS.USER}/profile`,
                true
            );

            console.log('用户资料响应数据:', response);

            if (response.code === 1) {
                if (response.data) {
                    const userInfo = {
                        ...response.data,
                        username: response.data.username,
                        full_name: response.data.full_name,
                        role: response.data.role,
                        phone: response.data.phone,
                        email: response.data.email,
                        department: response.data.department,
                        created_time: response.data.created_time,
                        last_login: response.data.last_login
                    };
                    sessionStorage.setItem('user', JSON.stringify(userInfo));
                    console.log('用户信息已更新到sessionStorage:', userInfo);
                }
                return response;
            } else {
                throw new Error(response.msg || '获取用户信息失败');
            }
        } catch (error) {
            console.error('获取用户信息失败:', error);
            const storedUser = sessionStorage.getItem('user');
            if (storedUser) {
                console.log('从sessionStorage获取用户信息');
                return {
                    code: 1,
                    msg: 'success',
                    data: JSON.parse(storedUser)
                };
            }
            throw error;
        }
    },

    // 更新用户信息
    async updateUser(userData) {
        try {
            console.log('更新用户信息:', userData);

            const response = await this.client.patch(
                `${API_CONFIG.ENDPOINTS.USER}/update`,
                userData,
                true
            );

            console.log('更新用户响应:', response);

            if (response.code === 1) {
                const storedUser = JSON.parse(sessionStorage.getItem('user') || '{}');
                const updatedUser = {
                    ...storedUser,
                    ...response.data
                };
                sessionStorage.setItem('user', JSON.stringify(updatedUser));
                console.log('sessionStorage已更新:', updatedUser);

                return response;
            } else {
                throw new Error(response.msg || '更新用户信息失败');
            }
        } catch (error) {
            console.error('更新用户信息失败:', error);
            throw error;
        }
    },

    // 修改密码
    async changePassword(oldPassword, newPassword) {
        try {
            const response = await this.client.put(
                `${API_CONFIG.ENDPOINTS.USER}/change_password`,
                {
                    old_password: oldPassword,
                    new_password: newPassword
                },
                true
            );

            if (response.code === 1) {
                return response;
            } else {
                throw new Error(response.msg || '修改密码失败');
            }
        } catch (error) {
            console.error('修改密码失败:', error);
            throw error;
        }
    }
};

// 数据管理器 - 同样需要移除 credentials
const DataManager = {
    // 获取分页用户数据
    async getUsersPage(page = 1, pageSize = 6) {
        try {
            const token = Utils.getToken();
            if (!token) {
                throw new Error('用户未登录');
            }

            const apiUrl = `${Utils.getApiBaseUrl()}/admin/users/page`;

            const response = await fetch(apiUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({
                    page: page,
                    size: pageSize
                })
                // 注意：这里也没有 credentials
            });

            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }

            const result = await response.json();

            if (result.code !== 1) {
                throw new Error(result.msg || '获取用户数据失败');
            }

            return result.data || { users: [], total_count: 0, total_pages: 1 };

        } catch (error) {
            console.error('获取用户数据失败:', error);
            throw error;
        }
    },

    // 添加用户
    async addUser(userData) {
        try {
            const token = Utils.getToken();
            if (!token) {
                throw new Error('用户未登录');
            }

            const apiUrl = `${Utils.getApiBaseUrl()}/admin/add_user`;

            const response = await fetch(apiUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify(userData)
            });

            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }

            const result = await response.json();

            if (result.code !== 1) {
                throw new Error(result.msg || '添加用户失败');
            }

            return result.data;

        } catch (error) {
            console.error('添加用户失败:', error);
            throw error;
        }
    },

    // 更新用户
    async updateUser(userData) {
        try {
            const token = Utils.getToken();
            if (!token) {
                throw new Error('用户未登录');
            }

            const apiUrl = `${Utils.getApiBaseUrl()}/admin/update_user`;

            const response = await fetch(apiUrl, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify(userData)
            });

            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }

            const result = await response.json();

            if (result.code !== 1) {
                throw new Error(result.msg || '更新用户失败');
            }

            return result.data;

        } catch (error) {
            console.error('更新用户失败:', error);
            throw error;
        }
    },

    // 删除用户
    async deleteUser(userId) {
        try {
            const token = Utils.getToken();
            if (!token) {
                throw new Error('用户未登录');
            }

            const apiUrl = `${Utils.getApiBaseUrl()}/admin/update_user`;

            const response = await fetch(apiUrl, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({
                    id: userId,
                    status: 0
                })
            });

            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }

            const result = await response.json();

            if (result.code !== 1) {
                throw new Error(result.msg || '删除用户失败');
            }

            return result.data;

        } catch (error) {
            console.error('删除用户失败:', error);
            throw error;
        }
    },

    // 根据ID获取用户
    async getUserById(userId) {
        try {
            const token = Utils.getToken();
            if (!token) {
                throw new Error('用户未登录');
            }

            const apiUrl = `${Utils.getApiBaseUrl()}/admin/user/${userId}`;

            const response = await fetch(apiUrl, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }

            const result = await response.json();

            if (result.code !== 1) {
                throw new Error(result.msg || '获取用户信息失败');
            }

            return result.data;

        } catch (error) {
            console.error('获取用户信息失败:', error);
            throw error;
        }
    },

    // 搜索用户
    async searchUsers(query) {
        try {
            const token = Utils.getToken();
            if (!token) {
                throw new Error('用户未登录');
            }

            console.warn('搜索用户功能暂未实现，后端需要添加搜索接口');

            const result = await this.getUsersPage(1, 100);

            if (!result || !result.users) {
                return [];
            }

            const lowerQuery = query.toLowerCase();
            const filteredUsers = result.users.filter(user => {
                return (
                    (user.username && user.username.toLowerCase().includes(lowerQuery)) ||
                    (user.full_name && user.full_name.toLowerCase().includes(lowerQuery)) ||
                    (user.department && user.department.toLowerCase().includes(lowerQuery)) ||
                    (user.email && user.email.toLowerCase().includes(lowerQuery)) ||
                    (user.phone && user.phone.includes(query))
                );
            });

            return filteredUsers;

        } catch (error) {
            console.error('搜索用户失败:', error);
            return [];
        }
    },

    // 获取所有用户
    async getAllUsers() {
        try {
            const token = Utils.getToken();
            if (!token) {
                throw new Error('用户未登录');
            }

            const apiUrl = `${Utils.getApiBaseUrl()}/admin/users`;

            const response = await fetch(apiUrl, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }

            const result = await response.json();

            if (result.code !== 1) {
                throw new Error(result.msg || '获取所有用户失败');
            }

            return result.data || [];

        } catch (error) {
            console.error('获取所有用户失败:', error);
            throw error;
        }
    },

    async searchUsersPage(query, page = 1, size = 6) {
        try {
            const token = Utils.getToken();
            if (!token) {
                throw new Error('用户未登录');
            }

            const apiUrl = `${Utils.getApiBaseUrl()}/admin/query`;

            const response = await fetch(apiUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({
                    data: query,
                    page: page,
                    size: size
                })
            });

            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }

            const result = await response.json();

            if (result.code !== 1 && result.code !== 200) {
                throw new Error(result.msg || result.detail || '搜索用户失败');
            }

            if (result.code === 200) {
                return result.data || {
                    total_count: 0,
                    total_pages: 0,
                    users: []
                };
            } else if (result.code === 1) {
                return result.data || {
                    total_count: 0,
                    total_pages: 0,
                    users: []
                };
            } else {
                throw new Error('搜索用户失败：未知的响应格式');
            }

        } catch (error) {
            console.error('搜索用户失败:', error);
            throw error;
        }
    }
};

// 导出到全局
window.DataManager = DataManager;
window.documentAPI = documentAPI;
window.userAPI = userAPI;
// 导出到全局
// window.adminAPI = adminAPI;