const express = require('express');
const fs = require('fs');
const app = express();
const port = process.env.PORT || 3000;

app.get('/', (req, res) => {
    try {
        const htaContent = fs.readFileSync('./file.hta', 'utf8');
        res.setHeader('Content-Type', 'text/html');
        res.send(htaContent);
    } catch (err) {
        res.send('Error loading HTA file. Make sure file.hta exists.');
    }
});

app.listen(port, () => {
    console.log(`Server running on port ${port}`);
});
