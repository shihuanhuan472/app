class APIClient {
    constructor() {
        this.baseUrl = API_CONFIG.BASE_URL;
        this.headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        };
        this.isRefreshing = false; // 新增：标记是否正在刷新token
        this.retryQueue = []; // 新增：存储待重试的请求
    }

    // 获取认证头
    getAuthHeaders() {
        // 改为从 localStorage 获取
        const token = localStorage.getItem('token');
        const headers = { ...this.headers };

        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        return headers;
    }

    async refreshToken() {
        // 防止重复刷新
        if (this.isRefreshing) {
            console.log('Token刷新正在进行中，等待...');
            await new Promise(resolve => setTimeout(resolve, 1000));
            return localStorage.getItem('token');
        }

        this.isRefreshing = true;

        try {
            // 改为从 localStorage 获取
            const refreshToken = localStorage.getItem('refresh_token');
            if (!refreshToken) {
                throw new Error('No refresh token available');
            }

            console.log('尝试刷新token...');

            const url = `${this.baseUrl}/auth/refresh`;
            const response = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    refresh_token: refreshToken
                })
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || errorData.msg || `刷新失败: HTTP ${response.status}`);
            }

            const result = await response.json();

            if (result.code === 1) {
                const newAccessToken = result.data.access_token;
                // 改为存储到 localStorage
                localStorage.setItem('token', newAccessToken);
                console.log('Token刷新成功并保存到 localStorage');

                // 处理等待队列中的请求
                while (this.retryQueue.length > 0) {
                    const retry = this.retryQueue.shift();
                    if (retry && typeof retry === 'function') {
                        retry(newAccessToken);
                    }
                }

                return newAccessToken;
            } else {
                throw new Error(result.msg || '刷新token失败');
            }
        } catch (error) {
            console.error('刷新token失败:', error);
            // 清除所有存储的token，跳转到登录页
            localStorage.removeItem('token');
            localStorage.removeItem('refresh_token');
            localStorage.removeItem('user');
            window.location.href = 'index.html';
            throw error;
        } finally {
            this.isRefreshing = false;
        }
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

            if (response.status === 401 && useAuth) {
                console.log('Token可能已过期，尝试刷新...');

                try {
                    const newToken = await this.refreshToken();

                    // 使用新token重试请求
                    options.headers = {
                        ...options.headers,
                        'Authorization': `Bearer ${newToken}`
                    };

                    const retryResponse = await fetch(url, options);

                    if (!retryResponse.ok) {
                        const errorData = await retryResponse.json().catch(() => ({}));
                        throw new Error(errorData.detail || errorData.msg || `HTTP ${retryResponse.status}`);
                    }

                    const responseData = await retryResponse.json();
                    console.log('重试成功，响应数据:', responseData);
                    return responseData;
                } catch (refreshError) {
                    console.error('刷新token并重试失败:', refreshError);
                    throw refreshError;
                }
            }

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

            if (response.status === 401 && useAuth) {
                console.log('Token可能已过期，尝试刷新...');

                try {
                    const newToken = await this.refreshToken();

                    // 使用新token重试请求
                    options.headers = {
                        ...options.headers,
                        'Authorization': `Bearer ${newToken}`
                    };

                    const retryResponse = await fetch(url, options);

                    if (!retryResponse.ok) {
                        const errorData = await retryResponse.json().catch(() => ({}));
                        throw new Error(errorData.detail || errorData.msg || `HTTP ${retryResponse.status}`);
                    }

                    const responseData = await retryResponse.json();
                    console.log('重试成功，响应数据:', responseData);
                    return responseData;
                } catch (refreshError) {
                    console.error('刷新token并重试失败:', refreshError);
                    throw refreshError;
                }
            }

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

            if (response.status === 401 && useAuth) {
                console.log('Token可能已过期，尝试刷新...');

                try {
                    const newToken = await this.refreshToken();

                    // 使用新token重试请求
                    options.headers = {
                        ...options.headers,
                        'Authorization': `Bearer ${newToken}`
                    };

                    const retryResponse = await fetch(url, options);

                    if (!retryResponse.ok) {
                        const errorData = await retryResponse.json().catch(() => ({}));
                        throw new Error(errorData.detail || errorData.msg || `HTTP ${retryResponse.status}`);
                    }

                    const responseData = await retryResponse.json();
                    console.log('重试成功，响应数据:', responseData);
                    return responseData;
                } catch (refreshError) {
                    console.error('刷新token并重试失败:', refreshError);
                    throw refreshError;
                }
            }

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

            if (response.status === 401 && useAuth) {
                console.log('Token可能已过期，尝试刷新...');

                try {
                    const newToken = await this.refreshToken();

                    // 使用新token重试请求
                    options.headers = {
                        ...options.headers,
                        'Authorization': `Bearer ${newToken}`
                    };

                    const retryResponse = await fetch(url, options);

                    if (!retryResponse.ok) {
                        const errorData = await retryResponse.json().catch(() => ({}));
                        throw new Error(errorData.detail || errorData.msg || `HTTP ${retryResponse.status}`);
                    }

                    const responseData = await retryResponse.json();
                    console.log('重试成功，响应数据:', responseData);
                    return responseData;
                } catch (refreshError) {
                    console.error('刷新token并重试失败:', refreshError);
                    throw refreshError;
                }
            }

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

            if (response.status === 401 && useAuth) {
                console.log('Token可能已过期，尝试刷新...');

                try {
                    const newToken = await this.refreshToken();

                    // 使用新token重试请求
                    options.headers = {
                        ...options.headers,
                        'Authorization': `Bearer ${newToken}`
                    };

                    const retryResponse = await fetch(url, options);

                    if (!retryResponse.ok) {
                        const errorData = await retryResponse.json().catch(() => ({}));
                        throw new Error(errorData.detail || errorData.msg || `HTTP ${retryResponse.status}`);
                    }

                    const responseData = await retryResponse.json();
                    console.log('重试成功，响应数据:', responseData);
                    return responseData;
                } catch (refreshError) {
                    console.error('刷新token并重试失败:', refreshError);
                    throw refreshError;
                }
            }

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

            if (response.status === 401 && sessionStorage.getItem('token')) {
                console.log('Token可能已过期，尝试刷新...');

                try {
                    const newToken = await this.refreshToken();

                    // 使用新token重试请求
                    options.headers = {
                        ...options.headers,
                        'Authorization': `Bearer ${newToken}`
                    };

                    const retryResponse = await fetch(url, options);

                    if (!retryResponse.ok) {
                        const errorData = await retryResponse.json().catch(() => ({}));
                        throw new Error(errorData.detail || errorData.msg || `HTTP ${retryResponse.status}`);
                    }

                    const responseData = await retryResponse.json();
                    console.log('重试成功，响应数据:', responseData);
                    return responseData;
                } catch (refreshError) {
                    console.error('刷新token并重试失败:', refreshError);
                    throw refreshError;
                }
            }

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

    // 上传文件（支持多文件，字段名 files）
    async uploadFiles(endpoint, files) {
        try {
            const url = `${this.baseUrl}${endpoint}`;
            const formData = new FormData();
            files.forEach(file => formData.append('files', file));

            const options = {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${localStorage.getItem('token')}`
                },
                body: formData
            };

            const response = await fetch(url, options);
            // 处理 401 刷新 token（与 uploadImages 相同逻辑，可复用或抽取公共方法）
            if (response.status === 401) {
                const newToken = await this.refreshToken();
                options.headers['Authorization'] = `Bearer ${newToken}`;
                const retryResponse = await fetch(url, options);
                return this._handleResponse(retryResponse);
            }
            return this._handleResponse(response);
        } catch (error) {
            console.error('上传文件失败:', error);
            throw error;
        }
    }

    // 辅助方法：统一处理响应
    async _handleResponse(response) {
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || errorData.msg || `HTTP ${response.status}`);
        }
        return response.json();
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
    },

    // 上传文件（批量）
    async uploadFiles(files) {
        try {
            const response = await this.client.uploadFiles(
                `${API_CONFIG.ENDPOINTS.DOCUMENTS}/upload_files`,
                files
            );
            if (response.code === 1) {
                return response.data; // UploadDocumentResponse
            } else {
                throw new Error(response.msg || '上传文件失败');
            }
        } catch (error) {
            console.error('上传文件失败:', error);
            throw error;
        }
    },

    // 解析文件
    async analyzeFiles(fileList, fileNames) {
        try {
            const response = await this.client.post(
                `${API_CONFIG.ENDPOINTS.DOCUMENTS}/analyze_files`,
                {
                    file_list: fileList,
                    file_name: fileNames
                },
                true
            );
            if (response.code === 1) {
                return response.data; // UploadDocumentResponse
            } else {
                throw new Error(response.msg || '解析文件失败');
            }
        } catch (error) {
            console.error('解析文件失败:', error);
            throw error;
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
                const tokenData = response.data;
                if (tokenData && tokenData.access_token) {
                    // 改为 localStorage
                    localStorage.setItem('token', tokenData.access_token);
                    console.log('Token 已保存到 localStorage');

                    // 存储 refresh_token
                    if (tokenData.refresh_token) {
                        localStorage.setItem('refresh_token', tokenData.refresh_token);
                        console.log('Refresh token 已保存到 localStorage');
                    }

                    // 存储用户信息
                    if (tokenData.user) {
                        localStorage.setItem('user', JSON.stringify(tokenData.user));
                        console.log('用户信息已存储到 localStorage:', tokenData.user);
                    }
                }
                return tokenData;
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

// 数据管理器
const DataManager = {
    client: new APIClient(), // 新增：创建APIClient实例

    // 获取分页用户数据
    async getUsersPage(page = 1, pageSize = 6) {
        try {
            const response = await this.client.post(
                '/admin/users/page',  // 使用相对路径
                {
                    page: page,
                    size: pageSize
                },
                true  // 需要认证
            );

            console.log('获取用户分页响应:', response);

            if (response.code === 1) {
                return response.data || { users: [], total_count: 0, total_pages: 1 };
            } else {
                throw new Error(response.msg || '获取用户数据失败');
            }
        } catch (error) {
            console.error('获取用户数据失败:', error);
            throw error;
        }
    },

    // 添加用户
    async addUser(userData) {
        try {
            const response = await this.client.post(
                '/admin/add_user',
                userData,
                true
            );

            if (response.code === 1) {
                return response.data;
            } else {
                throw new Error(response.msg || '添加用户失败');
            }
        } catch (error) {
            console.error('添加用户失败:', error);
            throw error;
        }
    },

    // 更新用户
    async updateUser(userData) {
        try {
            const response = await this.client.patch(
                '/admin/update_user',
                userData,
                true
            );

            if (response.code === 1) {
                return response.data;
            } else {
                throw new Error(response.msg || '更新用户失败');
            }
        } catch (error) {
            console.error('更新用户失败:', error);
            throw error;
        }
    },

    // 删除用户（通过更新status为0）
    async deleteUser(userId) {
        try {
            const response = await this.client.patch(
                '/admin/update_user',
                {
                    id: userId,
                    status: 0
                },
                true
            );

            if (response.code === 1) {
                return response.data;
            } else {
                throw new Error(response.msg || '删除用户失败');
            }
        } catch (error) {
            console.error('删除用户失败:', error);
            throw error;
        }
    },

    // 根据ID获取用户
    async getUserById(userId) {
        try {
            const response = await this.client.get(
                `/admin/user/${userId}`,  // 注意这里是路径参数
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

    // 搜索用户
    async searchUsers(query) {
        try {
            // 调用后端搜索接口
            return await this.searchUsersPage(query, 1, 100);
        } catch (error) {
            console.error('搜索用户失败:', error);
            return [];
        }
    },

    // 获取所有用户
    async getAllUsers() {
        try {
            const response = await this.client.get(
                '/admin/users',
                true
            );

            if (response.code === 1) {
                return response.data || [];
            } else {
                throw new Error(response.msg || '获取所有用户失败');
            }
        } catch (error) {
            console.error('获取所有用户失败:', error);
            throw error;
        }
    },

    // 搜索用户（分页）
    async searchUsersPage(query, page = 1, size = 6) {
        try {
            const response = await this.client.post(
                '/admin/query',
                {
                    data: query,
                    page: page,
                    size: size
                },
                true
            );

            console.log('搜索用户响应:', response);

            if (response.code === 1 || response.code === 200) {
                return response.data || {
                    total_count: 0,
                    total_pages: 0,
                    users: []
                };
            } else {
                throw new Error(response.msg || result.detail || '搜索用户失败');
            }
        } catch (error) {
            console.error('搜索用户失败:', error);
            throw error;
        }
    }
};

// 对话相关的 API
const conversationAPI = {
    client: new APIClient(),

    // 创建新对话
    async createConversation() {
        try {
            const response = await this.client.post(
                '/conversation/create',
                null,  // 不需要请求体
                true   // 需要认证
            );

            if (response.code === 1) {
                return response.data;
            } else {
                throw new Error(response.msg || '创建对话失败');
            }
        } catch (error) {
            console.error('创建对话失败:', error);
            throw error;
        }
    },

    // 获取对话历史
    async getHistory() {
        try {
            const response = await this.client.get(
                '/conversation/history',
                true
            );

            if (response.code === 1) {
                return response.data || [];
            } else {
                throw new Error(response.msg || '获取历史对话失败');
            }
        } catch (error) {
            console.error('获取历史对话失败:', error);
            return [];
        }
    },

    // 分页获取对话历史
    async getHistoryPage(pageParams = { page: 1, size: 20 }) {
        try {
            // 确保有默认值
            const page = pageParams.page || 1;
            const size = pageParams.size || 5;

            const response = await this.client.post(
                '/conversation/history/page',
                {
                    page: page,
                    size: size
                },
                true
            );

            // 兼容多种返回格式
            let resultData = null;
            if (response && typeof response === 'object') {
                if (response.code !== undefined) {
                    // 完整的 Result 对象
                    if (response.code === 1) {
                        resultData = response.data;
                    } else {
                        throw new Error(response.msg || '获取分页对话历史失败');
                    }
                } else {
                    // 直接的数据对象
                    resultData = response;
                }
            }

            return resultData || {
                total_count: 0,
                total_pages: 0,
                history: []
            };
        } catch (error) {
            console.error('获取分页对话历史失败:', error);
            return {
                total_count: 0,
                total_pages: 0,
                history: []
            };
        }
    },

    // 根据ID获取对话
    async getConversationById(id) {
        try {
            const response = await this.client.get(
                `/conversation/get_by_id/${id}`,
                true
            );

            if (response.code === 1) {
                return response.data;
            } else {
                console.error('获取对话失败:', response.msg);
                return null;
            }
        } catch (error) {
            console.error('获取对话失败:', error);
            return null;
        }
    },

    // 更新对话标题
    async updateTitle(id, newTitle) {
        try {
            const response = await this.client.put(
                `/conversation/update_title?id=${id}&new_title=${encodeURIComponent(newTitle)}`,
                null,
                true
            );

            if (response.code === 1) {
                return response.data;
            } else {
                throw new Error(response.msg || '更新对话标题失败');
            }
        } catch (error) {
            console.error('更新对话标题失败:', error);
            throw error;
        }
    },

    // 删除对话
    async deleteConversation(id) {
        try {
            const response = await this.client.delete(
                `/conversation/delete?id=${id}`,
                null,
                true
            );

            if (response.code === 1) {
                return true;
            } else {
                throw new Error(response.msg || '删除对话失败');
            }
        } catch (error) {
            console.error('删除对话失败:', error);
            throw error;
        }
    },

    // 搜索对话历史
    async searchConversations(query) {
        try {
            const response = await this.client.get(
                `/conversation/query?data=${encodeURIComponent(query)}`,
                true
            );

            if (response.code === 1) {
                return response.data || [];
            } else {
                console.error('搜索对话失败:', response.msg);
                return [];
            }
        } catch (error) {
            console.error('搜索对话失败:', error);
            return [];
        }
    }
};

// 消息相关的 API
const messageAPI = {
    client: new APIClient(),

    // 上传图片
    async uploadImages(files) {
        try {
            const response = await this.client.uploadImages(
                '/message/upload_images',
                files
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

    // 发送消息并获得回答
    async ask(messageData) {
        try {
            const response = await this.client.post(
                '/message/ask',
                messageData,
                true
            );

            if (response.code === 1) {
                return response.data || [];
            } else {
                throw new Error(response.msg || '发送消息失败');
            }
        } catch (error) {
            console.error('发送消息失败:', error);
            throw error;
        }
    },

    // 获取对话的消息
    async getMessagesByConversation(id) {
        try {
            const response = await this.client.get(
                `/message/get_by_conversation?id=${id}`,
                true
            );

            if (response.code === 1) {
                return response.data || [];
            } else {
                console.error('获取消息失败:', response.msg);
                return [];
            }
        } catch (error) {
            console.error('获取消息失败:', error);
            return [];
        }
    },

    async askStream(messageData, onChunk, onComplete, onError) {
    const url = `${this.client.baseUrl}/message/ask`;
    const token = localStorage.getItem('token');

    return new Promise((resolve, reject) => {
        fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                session_id: messageData.session_id,
                content_text: messageData.content_text,
                user_uploaded_images: messageData.user_uploaded_images,
                stream: true
            })
        })
        .then(async response => {
            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`HTTP ${response.status}: ${errorText}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            const processStream = async () => {
                try {
                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;

                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n');
                        buffer = lines.pop();

                        for (const line of lines) {
                            const trimmedLine = line.trim();
                            if (!trimmedLine) continue;

                            let jsonStr = trimmedLine;
                            if (trimmedLine.startsWith('data:')) {
                                jsonStr = trimmedLine.slice(5).trim();
                            }

                            try {
                                const parsed = JSON.parse(jsonStr);
                                // 结束标志
                                if (parsed.code === 1 && parsed.data === "true") {
                                    onComplete && onComplete();
                                    resolve(); // 流式正常结束
                                    return;
                                }
                                // 正常流数据
                                if (parsed.code === 1 && typeof parsed.answer === 'string') {
                                    onChunk && onChunk(parsed);
                                }
                            } catch (e) {
                                console.warn('解析 JSON 失败:', e);
                            }
                        }
                    }
                    // 如果正常读完流未收到结束消息，也认为完成
                    onComplete && onComplete();
                    resolve();
                } catch (err) {
                    onError && onError(err);
                    reject(err);
                }
            };

            processStream();
        })
        .catch(err => {
            onError && onError(err);
            reject(err);
        });
    });
}
};

// 导出到全局
window.conversationAPI = conversationAPI;
window.messageAPI = messageAPI;

// 导出到全局
window.DataManager = DataManager;
window.documentAPI = documentAPI;
window.userAPI = userAPI;
// 导出到全局
// window.adminAPI = adminAPI;