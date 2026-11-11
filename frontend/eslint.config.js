import js from '@eslint/js'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },
  js.configs.recommended,
  tseslint.configs.recommended,
  react.configs.flat.recommended,
  react.configs.flat['jsx-runtime'],
  reactHooks.configs.flat.recommended,
  jsxA11y.flatConfigs.recommended,
  {
    settings: { react: { version: 'detect' } },
    languageOptions: { globals: globals.browser },
    rules: {
      'react/no-danger': 'error', // D5: Serverdaten nur als Text
      'react/prop-types': 'off',
    },
  },
  { files: ['*.config.{js,ts}'], languageOptions: { globals: globals.node } },
)
