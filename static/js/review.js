class ReviewAPIClient {
    constructor() {
        this.baseUrl = API_CONFIG.BASE_URL;
        this.headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        };
        this.isRefreshing = false;
    }

    _formatErrorDetail(detail) {
        if (detail === null || detail === undefined) return '';
        if (typeof detail === 'string') return detail;

        if (Array.isArray(detail)) {
            const parts = detail
                .map((item) => (typeof item === 'string' ? item : JSON.stringify(item)))
                .filter(Boolean);
            return parts.join('；');
        }

        if (typeof detail === 'object') {
            // 后端常见结构：{ files: [{ file_name, detail, ... }] }
            if (Array.isArray(detail.files) && detail.files.length > 0) {
                const fileReasons = detail.files.map((f) => {
                    const fileName = f.file_name || f.file_path || '文件';
                    const reason = f.detail || f.message || '解析失败';
                    return `${fileName}: ${reason}`;
                });
                return fileReasons.join('；');
            }

            if (typeof detail.message === 'string' && detail.message.trim()) {
                return detail.message.trim();
            }

            try {
                return JSON.stringify(detail);
            } catch (_) {
                return String(detail);
            }
        }

        return String(detail);
    }

    _extractErrorMessage(errorData, fallback = '') {
        if (!errorData || typeof errorData !== 'object') return fallback;
        const detailMessage = this._formatErrorDetail(errorData.detail);
        if (detailMessage) return detailMessage;
        if (typeof errorData.msg === 'string' && errorData.msg.trim()) return errorData.msg.trim();
        if (typeof errorData.message === 'string' && errorData.message.trim()) return errorData.message.trim();
        return fallback;
    }

    _createApiError(data, statusCode, fallback) {
        const message = this._extractErrorMessage(data, fallback || `HTTP ${statusCode}`);
        const error = new Error(message);
        error.status = statusCode;
        error.payload = data;
        return error;
    }

    getAuthHeaders() {
        const token = localStorage.getItem('token');
        const headers = { ...this.headers };
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }
        return headers;
    }

    async refreshToken() {
        if (this.isRefreshing) {
            await new Promise(resolve => setTimeout(resolve, 500));
            return localStorage.getItem('token');
        }

        const refreshToken = localStorage.getItem('refresh_token');
        if (!refreshToken) {
            throw new Error('No refresh token available');
        }

        this.isRefreshing = true;
        try {
            const response = await fetch(`${this.baseUrl}/auth/refresh`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify({ refresh_token: refreshToken })
            });

            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.code !== 1 || !data.data?.access_token) {
                throw new Error(this._extractErrorMessage(data, '刷新token失败'));
            }

            const newToken = data.data.access_token;
            localStorage.setItem('token', newToken);
            if (data.data.refresh_token) {
                localStorage.setItem('refresh_token', data.data.refresh_token);
            }
            if (data.data.user) {
                localStorage.setItem('user', JSON.stringify(data.data.user));
            }
            return newToken;
        } finally {
            this.isRefreshing = false;
        }
    }

    async request(method, endpoint, body = null, extraHeaders = {}) {
        const options = {
            method,
            headers: {
                ...this.getAuthHeaders(),
                ...extraHeaders
            }
        };

        if (body !== null) {
            options.body = JSON.stringify(body);
        }

        let response = await fetch(`${this.baseUrl}${endpoint}`, options);
        let data = await response.json().catch(() => ({}));

        if (response.status === 401) {
            try {
                const newToken = await this.refreshToken();
                options.headers = {
                    ...options.headers,
                    Authorization: `Bearer ${newToken}`
                };
                response = await fetch(`${this.baseUrl}${endpoint}`, options);
                data = await response.json().catch(() => ({}));
            } catch (refreshError) {
                localStorage.removeItem('token');
                localStorage.removeItem('refresh_token');
                localStorage.removeItem('user');
                window.location.href = 'index.html';
                throw refreshError;
            }
        }

        if (!response.ok) {
            throw this._createApiError(data, response.status, `HTTP ${response.status}`);
        }

        if (typeof data.code !== 'undefined' && data.code !== 1) {
            throw this._createApiError(data, response.status, '请求失败');
        }

        return typeof data.data !== 'undefined' ? data.data : data;
    }

    get(endpoint) {
        return this.request('GET', endpoint);
    }

    post(endpoint, body) {
        return this.request('POST', endpoint, body);
    }

    put(endpoint, body) {
        return this.request('PUT', endpoint, body);
    }

    delete(endpoint, body = null) {
        return this.request('DELETE', endpoint, body);
    }
}

const reviewAPI = {
    client: new ReviewAPIClient(),

    async getById(userId) {
        if (!userId) {
            throw new Error('缺少用户id');
        }
        return this.client.request(
            'GET',
            `/review/get_by_id?id=${encodeURIComponent(userId)}`,
            null,
            { 'X-User-Id': String(userId) }
        );
    },

    async getMyReviews() {
        const currentUser = Utils.getCurrentUser() || {};
        return this.getById(currentUser.id);
    },

    async submitCreateReview(documentData) {
        return this.client.post('/review/create', {
            action_type: 1,
            ...documentData
        });
    },

    async submitUpdateReview(documentId, documentData) {
        return this.client.post('/review/create', {
            action_type: 2,
            document_id: Number(documentId),
            ...documentData
        });
    },

    async submitDeleteReview(documentId, reviewComment = null) {
        return this.client.post('/review/create', {
            action_type: 3,
            document_id: Number(documentId),
            review_comment: reviewComment
        });
    },

    async getReviewPendingList() {
        return this.client.get('/review/pending');
    },

    async getReviewAllList() {
        return this.client.get('/review/all');
    },

    async approveReview(reviewId, reviewComment = null) {
        return this.client.post(`/review/approve/${reviewId}`, {
            review_comment: reviewComment
        });
    },

    async rejectReview(reviewId, reviewComment = null) {
        return this.client.post(`/review/reject/${reviewId}`, {
            review_comment: reviewComment
        });
    },

    async withdrawReview(reviewId) {
        return this.client.post(`/review/withdraw/${reviewId}`, {});
    }
};


