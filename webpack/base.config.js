const path = require('path');
const BundleTracker = require('webpack-bundle-tracker');
const MiniCssExtractPlugin = require('mini-css-extract-plugin');
const Dotenv = require('dotenv-webpack');

module.exports = {
  target: 'web',
  context: path.join(__dirname, '../'),
  entry: {
    dashboard: path.resolve(
      __dirname,
      '../commcare_connect/static/js/dashboard',
    ),
    vendors: path.resolve(__dirname, '../commcare_connect/static/js/vendors'),
    tailwind: path.resolve(__dirname, '../tailwind/tailwind.css'),
    mapbox: path.resolve(__dirname, '../commcare_connect/static/js/mapbox'),
    tomselect: path.resolve(
      __dirname,
      '../commcare_connect/static/js/tomselect.js',
    ),
    'chat-widget': path.resolve(
      __dirname,
      '../commcare_connect/static/js/chat-widget.tsx',
    ),
    'code-editor': path.resolve(
      __dirname,
      '../commcare_connect/static/js/code-editor.tsx',
    ),
    'workflow-runner': path.resolve(
      __dirname,
      '../commcare_connect/static/js/workflow-runner.tsx',
    ),
    'pipeline-editor': path.resolve(
      __dirname,
      '../commcare_connect/static/js/pipeline-editor.tsx',
    ),
    'task-progress': path.resolve(
      __dirname,
      '../commcare_connect/static/js/task-progress.ts',
    ),
    'solicitation-chat': path.resolve(
      __dirname,
      '../commcare_connect/static/js/solicitation-chat.tsx',
    ),
  },
  output: {
    path: path.resolve(__dirname, '../commcare_connect/static/bundles/'),
    publicPath: '/static/bundles/',
    filename: 'js/[name]-bundle.js',
    chunkFilename: 'js/[name]-bundle.js',
  },
  plugins: [
    new BundleTracker({
      path: path.resolve(path.join(__dirname, '../')),
      filename: 'webpack-stats.json',
    }),
    new MiniCssExtractPlugin({ filename: 'css/[name].css' }),
    new Dotenv({ path: './.env' }),
  ],
  module: {
    rules: [
      {
        test: /\.(js|jsx|ts|tsx)$/,
        exclude: /node_modules/,
        loader: 'babel-loader',
      },
      {
        test: /\.css$/i,
        use: [
          MiniCssExtractPlugin.loader,
          {
            loader: 'css-loader',
            options: {
              sourceMap: false, // Strip source map references to avoid WhiteNoise errors with tomselect.js
            },
          },
          {
            loader: 'postcss-loader',
            options: {
              postcssOptions: {
                plugins: [
                  require('@tailwindcss/postcss'),
                  require('autoprefixer'),
                ],
              },
            },
          },
        ],
      },
      {
        test: /\.(scss|sass)$/i,
        use: [
          MiniCssExtractPlugin.loader,
          'css-loader',
          {
            loader: 'postcss-loader',
            options: {
              postcssOptions: {
                plugins: [
                  require('@tailwindcss/postcss'),
                  require('autoprefixer'),
                ],
              },
            },
          },
          'sass-loader',
        ],
      },
    ],
  },
  resolve: {
    modules: ['node_modules'],
    extensions: ['.js', '.jsx', '.ts', '.tsx'],
    alias: {
      '@': path.resolve(__dirname, '../'),
    },
  },
  devtool: 'eval-cheap-source-map',
};
