const basePath = "babybuddy/static/babybuddy/";

const config = {
  basePath: basePath,
  extrasConfig: {
    fonts: {
      dest: basePath + "font/",
      files: "babybuddy/static_src/fontello/font/*",
    },
    images: {
      dest: basePath + "img/",
      files: "**/static_src/img/**/*",
    },
    logo: {
      dest: basePath + "logo/",
      files: "babybuddy/static_src/logo/**/*",
    },
    root: {
      dest: basePath + "root/",
      files: "babybuddy/static_src/root/*",
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
      "babybuddy/static_src/js/babybuddy.js",
    ],
  },
  stylesConfig: {
    dest: basePath + "css/",
    app: "babybuddy/static_src/scss/babybuddy.scss",
    ignore: ["babybuddy.scss"],
  },
  testsConfig: {
    isolated: ["babybuddy.tests.tests_views.ViewsTestCase.test_password_reset"],
  },
  watchConfig: {
    scriptsGlob: ["*/static_src/js/**/*.js", "!babybuddy/static/js/"],
    stylesGlob: ["*/static_src/scss/**/*.scss"],
  },
};

export default config;
