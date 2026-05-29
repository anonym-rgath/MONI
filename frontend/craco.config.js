// craco.config.js
const path = require("path");

// Version als einzige Quelle der Wahrheit aus package.json beziehen und als
// REACT_APP_VERSION injizieren (CRA picks up REACT_APP_* aus process.env beim
// Build). So driftet die im Footer angezeigte Version nicht mehr vom Paket ab.
process.env.REACT_APP_VERSION = require("./package.json").version;

let webpackConfig = {
  eslint: {
    configure: {
      extends: ["plugin:react-hooks/recommended"],
      rules: {
        "react-hooks/rules-of-hooks": "error",
        "react-hooks/exhaustive-deps": "warn",
      },
    },
  },
  webpack: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
    configure: (webpackConfig) => {

      // Add ignored patterns to reduce watched directories
      webpackConfig.watchOptions = {
        ...webpackConfig.watchOptions,
        ignored: [
          '**/node_modules/**',
          '**/.git/**',
          '**/build/**',
          '**/dist/**',
          '**/coverage/**',
          '**/public/**',
        ],
      };

      return webpackConfig;
    },
  },
};

module.exports = webpackConfig;
