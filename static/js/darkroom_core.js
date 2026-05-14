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
            const name = (img.name || "").toLowerCase();
            const mime = (img.mimeType || "").toLowerCase();
            
            const isRaw = name.endsWith('.heic') || name.endsWith('.png') || mime.includes('heif') || mime.includes('png');
            const isPending = name.endsWith('.jpg') || name.endsWith('.jpeg') || mime.includes('jpeg');
            
            const nameWithoutExt = name.split('.')[0].trim();
            const alreadyDeveloped = isRaw && jpgNames.has(nameWithoutExt);

            if (filter === 'all') return !alreadyDeveloped;
            if (filter === 'completed') return img.associated;
            
            if (filter === 'raw') return !img.associated && isRaw && !alreadyDeveloped;
            if (filter === 'pending') return !img.associated && isPending && !isRaw;
            return true;
        });
    },

    // 3. GENERATORE PROXY URL
    getProxyUrl(imgId) {
        return `/api/drive/proxy/${imgId}`;
    },

    // 4. RICERCA PRODOTTI CON CACHE
    async fetchProducts(query = '', onlyPending = false, sheet = '') {
        const cacheKey = `${query}_${onlyPending}_${sheet}`;
        if (this.productCache.has(cacheKey)) {
            return this.productCache.get(cacheKey);
        }

        try {
            const url = `/api/darkroom/search-products?q=${encodeURIComponent(query)}&only_pending=${onlyPending}&sheet=${encodeURIComponent(sheet)}`;
            const res = await fetch(url);
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
