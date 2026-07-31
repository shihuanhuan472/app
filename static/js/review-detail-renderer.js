(function (window) {
    const labels = {
        problemIntro: '\u95ee\u9898\u63cf\u8ff0',
        causes: '\u539f\u56e0\u5206\u6790',
        evaluation: '\u6545\u969c\u8bc4\u4f30',
        inspection: '\u68c0\u67e5\u6b65\u9aa4',
        solutions: '\u89e3\u51b3\u65b9\u6848',
        keyPoints: '\u5173\u952e\u8981\u70b9',
        knowledgeDocument: '\u77e5\u8bc6\u5e93\u6587\u6863',
        sectionsUnit: '\u4e2a\u7ae0\u8282',
        imagesUnit: '\u5f20\u56fe\u7247',
        noContent: '\u65e0\u5185\u5bb9',
        image: '\u56fe\u7247',
        no: '\u65e0',
        knowledgeSections: '\u77e5\u8bc6\u5e93\u7ae0\u8282',
        noSections: '\u6682\u65e0\u7ae0\u8282\u5185\u5bb9',
        section: '\u7ae0\u8282'
    };

    const breakdownSections = [
        { title: labels.problemIntro, contentKey: 'problem_intro', imageKeys: ['image_urls_problem_intro', 'image_urls'] },
        { title: labels.causes, contentKey: 'causes', imageKeys: ['image_urls_causes'] },
        { title: labels.evaluation, contentKey: 'evaluation', imageKeys: ['image_urls_evaluation'] },
        { title: labels.inspection, contentKey: 'inspection', imageKeys: ['image_urls_inspection'] },
        { title: labels.solutions, contentKey: 'solutions', imageKeys: ['image_urls_solutions'] },
        { title: labels.keyPoints, contentKey: 'key_points', imageKeys: ['image_urls_key_points'] }
    ];

    function escapeHtml(text) {
        const value = text == null ? '' : String(text);
        return value
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function normalizeLibraryType(value) {
        return String(value || '').trim().toLowerCase() === 'knowledge' ? 'knowledge' : 'breakdown';
    }

    function getReviewLibraryType(item) {
        const explicit = normalizeLibraryType(
            item && (item.library_type || item.document_library_type || item.review_library_type)
        );
        if (explicit === 'knowledge') return 'knowledge';
        return Array.isArray(item && item.sections) ? 'knowledge' : 'breakdown';
    }

    function getReviewStorageType(item) {
        return normalizeLibraryType(
            item && (item.review_library_type || getReviewLibraryType(item))
        );
    }

    function normalizeImageUrls(value) {
        if (!value) return [];
        if (Array.isArray(value)) {
            return value.map(item => String(item || '').trim()).filter(Boolean);
        }
        if (typeof value !== 'string') {
            return [];
        }

        const raw = value.trim();
        if (!raw) return [];
        if (raw.startsWith('[')) {
            try {
                const parsed = JSON.parse(raw);
                if (Array.isArray(parsed)) return normalizeImageUrls(parsed);
            } catch (error) {
                // Fall through to comma splitting for legacy values.
            }
        }
        return raw.split(',').map(item => item.trim()).filter(Boolean);
    }

    function getAssetUrl(path) {
        if (!path) return '';
        if (/^(https?:)?\/\//.test(path) || String(path).startsWith('data:')) return path;
        const base = ((window.API_CONFIG && window.API_CONFIG.BASE_URL) || '')
            .replace(/\/api\/v1\/?$/, '')
            .replace(/\/$/, '');
        const cleanPath = String(path).replace(/^\/+/, '');
        return base ? `${base}/${cleanPath}` : `/${cleanPath}`;
    }

    function getKnowledgeSections(item) {
        return (Array.isArray(item && item.sections) ? item.sections : [])
            .slice()
            .sort((a, b) => (a.section_index ?? 0) - (b.section_index ?? 0));
    }

    function firstFilledText(values) {
        for (const value of values) {
            const text = String(value || '').trim();
            if (text) return text;
        }
        return '';
    }

    function getPreviewText(item, maxLength = 100) {
        const libraryType = getReviewLibraryType(item);
        let text = '';
        if (libraryType === 'knowledge') {
            const sections = getKnowledgeSections(item);
            const firstTextSection = sections.find(section => String(section.plain_text || '').trim());
            const imageCount = sections.reduce((sum, section) => sum + normalizeImageUrls(section.image_urls).length, 0);
            text = firstTextSection
                ? String(firstTextSection.plain_text || '').trim()
                : (sections.length || imageCount
                    ? `${labels.knowledgeDocument}: ${sections.length} ${labels.sectionsUnit}, ${imageCount} ${labels.imagesUnit}`
                    : '');
        }

        if (!text) {
            text = firstFilledText([
                item && item.problem_intro,
                item && item.causes,
                item && item.evaluation,
                item && item.inspection,
                item && item.solutions,
                item && item.key_points,
                item && item.title
            ]);
        }
        if (!text) return labels.noContent;
        return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
    }

    function renderImageGrid(rawValue, options = {}) {
        const urls = normalizeImageUrls(rawValue);
        if (!urls.length || options.includeImages === false) return '';
        const gridClass = options.imageGridClass || 'image-grid';
        const itemClass = options.imageItemClass || 'image-item';
        return `
            <div class="${gridClass}">
                ${urls.map(url => {
                    const fullUrl = getAssetUrl(url);
                    return `
                        <a class="${itemClass}" href="${escapeHtml(fullUrl)}" target="_blank" rel="noopener noreferrer">
                            <img src="${escapeHtml(fullUrl)}" alt="${labels.image}" loading="lazy" onerror="this.style.opacity='0.4'">
                        </a>
                    `;
                }).join('')}
            </div>
        `;
    }

    function renderTextBlock(text, options = {}) {
        const textClass = options.textClass || 'section-card';
        const emptyClass = options.emptyClass || `${textClass} empty`;
        const emptyText = options.emptyText || labels.no;
        const value = String(text || '').trim();
        if (!value) return `<div class="${emptyClass}">${escapeHtml(emptyText)}</div>`;
        return `<div class="${textClass}">${escapeHtml(value)}</div>`;
    }

    function renderSection(title, bodyHtml, options = {}) {
        const sectionClass = options.sectionClass || 'detail-section';
        const headingTag = options.headingTag || 'h3';
        return `
            <div class="${sectionClass}">
                <${headingTag}>${escapeHtml(title)}</${headingTag}>
                ${bodyHtml}
            </div>
        `;
    }

    function renderBreakdownSections(item, options = {}) {
        return breakdownSections.map(section => {
            const imageValue = firstFilledText(section.imageKeys.map(key => item && item[key]));
            return renderSection(
                section.title,
                `${renderTextBlock(item && item[section.contentKey], options)}${renderImageGrid(imageValue, options)}`,
                options
            );
        }).join('');
    }

    function renderKnowledgeSections(item, options = {}) {
        const sections = getKnowledgeSections(item);
        if (!sections.length) {
            return renderSection(
                options.emptyKnowledgeTitle || labels.knowledgeSections,
                `<div class="${options.emptyClass || 'section-card empty'}">${escapeHtml(options.emptyKnowledgeMessage || labels.noSections)}</div>`,
                options
            );
        }

        return sections.map((section, index) => {
            const title = section.section_title || `${labels.section} ${index + 1}`;
            const marker = section.section_type || String(index + 1);
            const imageCount = normalizeImageUrls(section.image_urls).length;
            const metaHtml = options.showImageCount
                ? `<div class="${options.metaClass || 'knowledge-section-meta'}">${labels.image} ${imageCount} ${labels.imagesUnit}</div>`
                : '';
            const bodyText = String(section.plain_text || '').trim();
            const preview = options.previewLength && bodyText.length > options.previewLength
                ? `${bodyText.slice(0, options.previewLength)}...`
                : bodyText;
            return renderSection(
                `${marker} ${title}`,
                `${metaHtml}${renderTextBlock(preview, options)}${renderImageGrid(section.image_urls, options)}`,
                options
            );
        }).join('');
    }

    function renderReviewContent(item, options = {}) {
        return getReviewLibraryType(item) === 'knowledge'
            ? renderKnowledgeSections(item, options)
            : renderBreakdownSections(item, options);
    }

    window.ReviewDetailRenderer = {
        escapeHtml,
        getReviewLibraryType,
        getReviewStorageType,
        getPreviewText,
        labels,
        normalizeImageUrls,
        renderBreakdownSections,
        renderImageGrid,
        renderKnowledgeSections,
        renderReviewContent,
        renderTextBlock
    };
})(window);
