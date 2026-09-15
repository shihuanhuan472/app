// Shared frontend application identity and display text.
(function (window) {
    const values = {
        fullName: '设备维修辅助系统',
        shortName: '维修辅助系统',
        tagline: '智能维修解决方案平台',
        knowledgeTitle: '维修知识库',
        knowledgeSubtitle: '技术文档与故障案例',
        knowledgeDocumentTitle: '维修知识库文档',
        copyrightYear: '2026',
        version: 'v1.0',
    };

    function interpolate(template, extraValues) {
        const context = Object.assign({}, values, extraValues || {});
        return String(template || '').replace(/\{\{([A-Za-z0-9_]+)\}\}/g, function (_, key) {
            return context[key] == null ? '' : String(context[key]);
        });
    }

    const config = Object.freeze(Object.assign({}, values, {
        interpolate,
        formatDocumentTitle(title, fallback) {
            return `${title || fallback || '文档详情'} - ${values.shortName}`;
        },
    }));

    function applyAppConfig(root) {
        const scope = root || document;
        scope.querySelectorAll('[data-app-text]').forEach(function (element) {
            const key = element.getAttribute('data-app-text');
            if (Object.prototype.hasOwnProperty.call(values, key)) {
                element.textContent = values[key];
            }
        });
        scope.querySelectorAll('[data-app-template]').forEach(function (element) {
            element.textContent = interpolate(element.getAttribute('data-app-template'));
        });
    }

    window.APP_CONFIG = config;
    window.applyAppConfig = applyAppConfig;

    // The title element is already parsed when this script is loaded in <head>.
    applyAppConfig(document);
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { applyAppConfig(document); });
    }
})(window);
