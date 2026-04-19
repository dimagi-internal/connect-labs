## XForm XML Structure

Every form is an XForm document with this skeleton:

```xml
<?xml version="1.0"?>
<h:html xmlns:h="http://www.w3.org/1999/xhtml"
        xmlns="http://www.w3.org/2002/xforms"
        xmlns:jr="http://openrosa.org/javarosa">
<h:head>
  <h:title>Form Title</h:title>
  <model>
    <instance>
      <data xmlns:jrm="http://dev.commcarehq.org/jr/xforms"
            xmlns="http://openrosa.org/formdesigner/UNIQUE-FORM-ID"
            uiVersion="1" version="1" name="Form Title">
        <!-- One element per question, matching the body refs -->
        <question_id/>
        <case/>
      </data>
    </instance>
    <itext>
      <translation lang="en" default="">
        <text id="question_id-label"><value>Question Label</value></text>
      </translation>
    </itext>
    <!-- Bindings define data types, constraints, relevance, and calculations -->
    <bind nodeset="/data/question_id" type="xsd:string"/>
    <bind nodeset="/data/age" type="xsd:int"
          constraint=". > 0 and . &lt; 120"
          jr:constraintMsg="Age must be between 1 and 119"/>
    <bind nodeset="/data/bmi" type="xsd:decimal"
          calculate="/data/weight div (/data/height * /data/height)"/>
    <bind nodeset="/data/followup_needed" type="xsd:string"
          relevant="/data/risk_level = 'high'"/>
  </model>
</h:head>
<h:body>
  <!-- Input types: input, select1, select, group, repeat, trigger -->
  <input ref="/data/name"><label ref="jr:itext('name-label')"/></input>
  <select1 ref="/data/gender">
    <label ref="jr:itext('gender-label')"/>
    <item><label ref="jr:itext('gender-male')"/><value>male</value></item>
    <item><label ref="jr:itext('gender-female')"/><value>female</value></item>
  </select1>
  <select ref="/data/symptoms"><!-- multi-select --></select>
  <group>
    <label ref="jr:itext('group-label')"/>
    <!-- nested questions -->
  </group>
  <group>
    <label ref="jr:itext('repeat-label')"/>
    <repeat nodeset="/data/visits"><!-- repeated questions --></repeat>
  </group>
</h:body>
</h:html>
```

**Key rules:**

- Every `<bind>` nodeset must match an element in `<instance>`
- `type` values: `xsd:string`, `xsd:int`, `xsd:decimal`, `xsd:date`, `xsd:dateTime`, `xsd:boolean`
- `relevant` = skip logic (XPath expression), `constraint` = validation
- `calculate` = auto-computed value (XPath expression)
- `required="true()"` makes a field mandatory
- itext IDs must be unique; use `{question_id}-label` convention
- The `xmlns` on `<data>` must be a unique URI per form (use `http://openrosa.org/formdesigner/{UUID}`)

## Case XML (inside XForm)

Case operations live inside the form's `<instance>` and are controlled by a case block:

```xml
<!-- In <instance> -->
<case case_id="" date_modified="" user_id="">
  <create><case_type/><case_name/><owner_id/></create>
  <update><property_name/></update>
  <close/>
  <index><parent case_type="parent_type" relationship="child"/></index>
</case>

<!-- In <bind> -->
<bind nodeset="/data/case/create/case_type" calculate="'patient'"/>
<bind nodeset="/data/case/create/case_name" calculate="/data/name"/>
<bind nodeset="/data/case/update/last_visit" calculate="/data/visit_date"/>
```

**Case operations:**

- `create` — opens a new case; requires `case_type`, `case_name`, `owner_id`
- `update` — sets case properties; each child element name = property name
- `close` — closes the case (empty element)
- `index` — creates parent/child relationship between cases

## Suite XML Structure

The suite.xml defines app navigation, menus, and case selection:

```xml
<suite version="1">
  <detail id="m0_case_short"><!-- Case list (short) -->
    <title><text><locale id="cchq.case"/></text></title>
    <field>
      <header><text><locale id="name.header"/></text></header>
      <template><text><xpath function="name"/></text></template>
    </field>
  </detail>

  <detail id="m0_case_long"><!-- Case detail (long) -->
    <title><text><locale id="cchq.case"/></text></title>
    <field>
      <header><text><locale id="name.header"/></text></header>
      <template><text><xpath function="name"/></text></template>
    </field>
  </detail>

  <entry>
    <form>http://openrosa.org/formdesigner/FORM-UUID</form>
    <command id="m0-f0">
      <text><locale id="forms.m0f0"/></text>
    </command>
    <!-- For follow-up forms that need a case -->
    <session>
      <datum id="case_id" nodeset="instance('casedb')/casedb/case[@case_type='patient'][@status='open']"
             value="./@case_id" detail-select="m0_case_short" detail-confirm="m0_case_long"/>
    </session>
  </entry>

  <menu id="m0">
    <text><locale id="modules.m0"/></text>
    <command id="m0-f0"/>
    <command id="m0-f1"/>
  </menu>
</suite>
```

**Key rules:**

- Menu `id` format: `m0`, `m1`, etc. (module index)
- Command `id` format: `m0-f0`, `m0-f1` (module-form index)
- Detail `id` format: `m0_case_short`, `m0_case_long`
- `datum` `nodeset` filters cases by `@case_type` and `@status='open'`
- Registration forms have NO `<session>` datum (they create cases)
- Follow-up forms require a `<session>` with a case datum

## Common Patterns

**Registration form:** Creates a new case, no case selection needed.

- XForm: has `<create>` block with case_type, case_name, owner_id
- Suite entry: NO `<session>` datum

**Follow-up form:** Updates an existing case, requires case list.

- XForm: has `<update>` block, binds case properties
- Suite entry: HAS `<session>` with datum for case selection
- Suite: needs `detail-select` (case list) and `detail-confirm` (case detail)

**Case list columns:** Each `<field>` in `<detail id="..._short">` shows one column. The `xpath function` references a case property name.

**Multi-language:** Each `<translation>` block in itext has a `lang` attribute. All text IDs must appear in all translations.

**Parent-child cases:** Use `<index>` in the child case's create block. The parent datum must appear in the suite session BEFORE the child datum.
