// js/config.js
const API_CONFIG = {
    // 后端 API 基础地址
    BASE_URL: '',

    // 各 API 端点
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
        USER: '/user',  // 用户相关接口基础路径
        USERS: '/user', // 别名，保持兼容
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
    }
};
