// js/config.js
function normalizeApiBaseUrl(baseUrl) {
    const normalized = String(baseUrl || '').trim().replace(/\/+$/, '');
    if (!normalized) return '';
    return /\/api\/v1$/i.test(normalized) ? normalized : `${normalized}/api/v1`;
}

function resolveApiBaseUrl() {
    const explicitBaseUrl = normalizeApiBaseUrl(
        window.MAINTENANCE_API_BASE_URL || window.API_BASE_URL || ''
    );
    if (explicitBaseUrl) return explicitBaseUrl;

    const origin = window.location && window.location.origin && window.location.origin !== 'null'
        ? window.location.origin
        : '';
    return normalizeApiBaseUrl(origin);
}

function resolveStaticBaseUrl() {
    const origin = window.location && window.location.origin && window.location.origin !== 'null'
        ? window.location.origin
        : '';
    return origin.replace(/\/$/, '');
}

const API_CONFIG = {
    // 后端 API 基础地址
    BASE_URL: resolveApiBaseUrl(),
    STATIC_BASE_URL: resolveStaticBaseUrl(),

   
    ENDPOINTS: {
        // 文档相关
        DOCUMENTS: '/document',
        DOCUMENT_PAGE: '/document/page',
        DOCUMENT_BY_ID: '/document/get_by_id/{id}',
        DOCUMENT_ADD: '/document/add',
        DOCUMENT_UPLOAD_IMAGES: '/document/upload_images',
        DOCUMENT_DELETE_IMAGE: '/document/delete_image',

        // 用户相关
        LOGIN: '/auth/login',
        REGISTER: '/auth/register',
        USER: '/user',  // 用户相关接口基础路径
        USERS: '/user', // 别名
        USER_PROFILE: '/user/profile',  // 获取用户资料
    },

    // 请求超时时间（毫秒）
    TIMEOUT: 30000,

    // 获取完整 API URL
    getUrl(endpoint, params = {}) {
        let url = `${this.BASE_URL}${endpoint}`;

        // 替换路径参数
        for (const [key, value] of Object.entries(params)) {
            url = url.replace(`{${key}}`, value);
        }

        return url;
    },

    getAssetUrl(path) {
        if (!path) return '';
        const value = String(path).trim();
        if (/^(data:|blob:|https?:\/\/|\/\/)/i.test(value)) {
            return value;
        }

        const base = (this.STATIC_BASE_URL || this.BASE_URL.replace(/\/api\/v1\/?$/, '')).replace(/\/$/, '');
        return `${base}/${value.replace(/^\/+/, '')}`;
    }
};

window.API_CONFIG = API_CONFIG;
