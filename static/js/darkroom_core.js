/**
 * ATELIER AI - DARKROOM CORE MODULE
 * Gestione sincronizzazione Drive e Rendering High-Performance.
 */

const DarkroomCore = {
    allImages: [],
    currentFilter: 'all',
    productCache: new Map(),

    // 1. SINCRONIZZAZIONE DATI
    async syncDrive(forced = false) {
        const url = forced ? `/api/darkroom/images?refresh=true&_=${Date.now()}` : `/api/darkroom/images?_=${Date.now()}`;
        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP Error: ${res.status}`);
            this.allImages = await res.json();
            return this.allImages;
        } catch (err) {
            console.error("DarkroomCore Sync Error:", err);
            throw err;
        }
    },

    // 2. LOGICA FILTRAGGIO
    getFilteredImages(filter, jpgNames) {
        return this.allImages.filter(img => {
            if (filter === 'all') return true;
            if (filter === 'completed') return img.associated;
            
            const isRaw = (img.name || "").toLowerCase().endsWith('.heic') || (img.mimeType || "").includes('heif') || (img.mimeType || "").includes('png');
            const nameWithoutExt = (img.name || "").split('.')[0].toLowerCase();
            
            if (filter === 'raw') return !img.associated && isRaw && !jpgNames.has(nameWithoutExt);
            if (filter === 'pending') return !img.associated && (img.name || "").toLowerCase().match(/\.(jpg|jpeg)$/);
            return true;
        });
    },

    // 3. GENERATORE PROXY URL
    getProxyUrl(imgId) {
        return `/api/drive/proxy/${imgId}`;
    },

    // 4. RICERCA PRODOTTI CON CACHE
    async fetchProducts(query = '', onlyPending = false) {
        const cacheKey = `${query}_${onlyPending}`;
        if (this.productCache.has(cacheKey)) {
            return this.productCache.get(cacheKey);
        }

        try {
            const res = await fetch(`/api/darkroom/search-products?q=${encodeURIComponent(query)}&only_pending=${onlyPending}`);
            const products = await res.json();
            this.productCache.set(cacheKey, products);
            return products;
        } catch (err) {
            console.error("DarkroomCore Product Search Error:", err);
            return [];
        }
    }
};

window.DarkroomCore = DarkroomCore;
