const basePath = "housetrained/static/housetrained/";

const config = {
  basePath: basePath,
  extrasConfig: {
    fonts: {
      dest: basePath + "font/",
      files: "housetrained/static_src/fontello/font/*",
    },
    images: {
      dest: basePath + "img/",
      files: "**/static_src/img/**/*",
    },
    logo: {
      dest: basePath + "logo/",
      files: "housetrained/static_src/logo/**/*",
    },
    root: {
      dest: basePath + "root/",
      files: "housetrained/static_src/root/*",
    },
  },
  scriptsConfig: {
    dest: basePath + "js/",
    vendor: [
      "node_modules/pulltorefreshjs/dist/index.umd.js",
      "node_modules/jquery/dist/jquery.js",
      "node_modules/@popperjs/core/dist/umd/popper.js",
      "node_modules/bootstrap/dist/js/bootstrap.js",
    ],
    app: [
      "housetrained/static_src/js/housetrained.js",
    ],
  },
  stylesConfig: {
    dest: basePath + "css/",
    app: "housetrained/static_src/scss/housetrained.scss",
    ignore: ["housetrained.scss"],
  },
  testsConfig: {
    isolated: ["housetrained.tests.tests_views.ViewsTestCase.test_password_reset"],
  },
  watchConfig: {
    scriptsGlob: ["*/static_src/js/**/*.js", "!housetrained/static/js/"],
    stylesGlob: ["*/static_src/scss/**/*.scss"],
  },
};

export default config;
